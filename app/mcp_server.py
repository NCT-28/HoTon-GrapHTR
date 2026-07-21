"""MCP tool surface exposed to hoton-lmr (and any other MCP client)."""

import uuid
from dataclasses import dataclass

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel
from qdrant_client import QdrantClient

from app.context import apply_self_consistency, build_full_context
from app.grading import crag_correct
from app.hyde import generate_hypothetical_answer
from app.memory import extract_and_store_memories, retrieve_memories
from app.profile import get_or_create_profile, update_profile_from_message
from app.react import run_multi_step_retrieval
from app.retrieval import retrieve_chunks
from app.routing import QueryComplexity, classify_query


@dataclass
class ToolContext:
    client: QdrantClient
    embedder: object
    llm: object
    web_search_fn: object  # Callable[[str], Awaitable[list[str]]]


def build_tool_context(client: QdrantClient, embedder, llm, web_search_fn) -> ToolContext:
    return ToolContext(client=client, embedder=embedder, llm=llm, web_search_fn=web_search_fn)


class ChunkOut(BaseModel):
    id: str
    content: str
    document_title: str | None
    source_url: str | None
    similarity: float
    document_expired: bool


class RagContextResult(BaseModel):
    context_text: str
    chunks_used: int
    memories_used: int
    # Individual chunks (not just the merged text) — hoton-lmr's Phase 5 citation
    # feature ("rag_sources" SSE event + per-turn chunk-id logging) needs
    # document_title/source_url/similarity/id per chunk, not a pre-joined string.
    chunks: list[ChunkOut]


class RetrieveChunksResult(BaseModel):
    chunks: list[ChunkOut]


def _to_chunk_out(chunks) -> list[ChunkOut]:
    return [
        ChunkOut(
            id=c.id,
            content=c.content,
            document_title=c.document_title,
            source_url=c.source_url,
            similarity=c.similarity,
            document_expired=c.document_expired,
        )
        for c in chunks
    ]


class ExtractMemoriesResult(BaseModel):
    stored: int


class UpdateProfileResult(BaseModel):
    updated: bool


class EmbedTextResult(BaseModel):
    embedding: list[float]


RAG_TOP_K = 5
RAG_MIN_SIMILARITY = 0.65
MEMORY_TOP_K = 5
MEMORY_MIN_SIMILARITY = 0.6


async def get_rag_context_impl(ctx: ToolContext, user_id: str, query: str) -> RagContextResult:
    uid = uuid.UUID(user_id)
    complexity = classify_query(ctx.llm, query)

    if complexity == QueryComplexity.DIRECT:
        return RagContextResult(context_text="", chunks_used=0, memories_used=0, chunks=[])

    if complexity == QueryComplexity.MULTI:
        chunks, memories = run_multi_step_retrieval(
            ctx.client, ctx.embedder, ctx.llm, uid, query, RAG_TOP_K, RAG_MIN_SIMILARITY
        )
        chunks = await crag_correct(ctx.llm, ctx.web_search_fn, query, chunks)
    else:  # SINGLE
        hyde_query = generate_hypothetical_answer(ctx.llm, query)
        chunks = retrieve_chunks(ctx.client, ctx.embedder, uid, hyde_query, RAG_TOP_K, RAG_MIN_SIMILARITY)
        chunks = await crag_correct(ctx.llm, ctx.web_search_fn, query, chunks)
        memories = retrieve_memories(ctx.client, ctx.embedder, uid, query, MEMORY_TOP_K, MEMORY_MIN_SIMILARITY)

    apply_self_consistency(memories, query)
    profile = get_or_create_profile(ctx.client, uid)
    context_text = build_full_context(chunks, memories, profile)
    return RagContextResult(
        context_text=context_text,
        chunks_used=len(chunks),
        memories_used=len(memories),
        chunks=_to_chunk_out(chunks),
    )


def retrieve_chunks_impl(
    ctx: ToolContext, user_id: str, query: str, top_k: int = 5, min_similarity: float = 0.0
) -> RetrieveChunksResult:
    chunks = retrieve_chunks(ctx.client, ctx.embedder, uuid.UUID(user_id), query, top_k, min_similarity)
    return RetrieveChunksResult(chunks=_to_chunk_out(chunks))


def extract_and_store_memories_impl(
    ctx: ToolContext, user_id: str, user_message: str, assistant_message: str
) -> ExtractMemoriesResult:
    stored = extract_and_store_memories(
        ctx.client, ctx.embedder, ctx.llm, uuid.UUID(user_id), user_message, assistant_message
    )
    return ExtractMemoriesResult(stored=stored)


def update_profile_from_message_impl(ctx: ToolContext, user_id: str, user_message: str) -> UpdateProfileResult:
    update_profile_from_message(ctx.client, uuid.UUID(user_id), user_message)
    return UpdateProfileResult(updated=True)


def embed_text_impl(ctx: ToolContext, text: str) -> EmbedTextResult:
    return EmbedTextResult(embedding=ctx.embedder.embed_single(text))


def build_mcp_server(ctx: ToolContext) -> FastMCP:
    mcp = FastMCP("hoton-rag", stateless_http=True, json_response=True)

    @mcp.tool()
    async def get_rag_context(user_id: str, query: str) -> RagContextResult:
        """Retrieve merged profile/memory/knowledge context for a chat turn."""
        return await get_rag_context_impl(ctx, user_id, query)

    @mcp.tool()
    def retrieve_chunks(user_id: str, query: str, top_k: int = 5, min_similarity: float = 0.0) -> RetrieveChunksResult:
        """Raw chunk search, for workflow rag_query nodes."""
        return retrieve_chunks_impl(ctx, user_id, query, top_k, min_similarity)

    @mcp.tool()
    def extract_and_store_memories(user_id: str, user_message: str, assistant_message: str) -> ExtractMemoriesResult:
        """Post-turn hook: extract and store facts/preferences from a conversation turn."""
        return extract_and_store_memories_impl(ctx, user_id, user_message, assistant_message)

    @mcp.tool()
    def update_profile_from_message(user_id: str, user_message: str) -> UpdateProfileResult:
        """Post-turn hook: update the user's profile signals from their message."""
        return update_profile_from_message_impl(ctx, user_id, user_message)

    @mcp.tool()
    def embed_text(text: str) -> EmbedTextResult:
        """Raw embedding, for workflow embedding nodes."""
        return embed_text_impl(ctx, text)

    return mcp
