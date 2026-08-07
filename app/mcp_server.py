"""MCP tool surface exposed to MCP clients."""

import asyncio
import uuid
from dataclasses import dataclass

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel
from qdrant_client import QdrantClient

from app.rag.context import apply_self_consistency, build_full_context
from app.agentic.grading import crag_correct
from app.agentic.hyde import generate_hypothetical_answer
from app.rag.memory import extract_and_store_memories, retrieve_memories
from app.rag.profile import get_or_create_profile, update_profile_from_message
from app.agentic.react import run_multi_step_retrieval
from app.rag.retrieval import retrieve_chunks
from app.agentic.routing import QueryComplexity, classify_query
from app.graph.code_graph_store import GraphStore
from app.graph.ingest import IngestCodebaseResult, ingest_codebase_impl
from app.dashboard.tracker import track_usage
from app.dashboard.usage_store import UsageStore


@dataclass
class ToolContext:
    client: QdrantClient
    embedder: object
    llm: object
    web_search_fn: object  # Callable[[str], Awaitable[list[str]]]
    graph_store: GraphStore | None = None
    usage_store: UsageStore | None = None


def build_tool_context(
    client: QdrantClient, embedder, llm, web_search_fn,
    graph_store: GraphStore | None = None, usage_store: UsageStore | None = None,
) -> ToolContext:
    return ToolContext(
        client=client, embedder=embedder, llm=llm, web_search_fn=web_search_fn,
        graph_store=graph_store, usage_store=usage_store,
    )


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
    # Individual chunks (not just the merged text) — downstream citation features
    # (e.g. a "rag_sources" SSE event + per-turn chunk-id logging) need
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
    # This handler is `async def`, so anything called directly (not via
    # asyncio.to_thread) blocks the single event loop for its full duration —
    # LLM inference and Qdrant network search are neither. Offloading each
    # blocking step to a thread lets concurrent requests interleave instead of
    # fully serializing behind one caller's multi-step retrieval chain.
    uid = uuid.UUID(user_id)
    complexity = await asyncio.to_thread(classify_query, ctx.llm, query)

    if complexity == QueryComplexity.DIRECT:
        return RagContextResult(context_text="", chunks_used=0, memories_used=0, chunks=[])

    if complexity == QueryComplexity.MULTI:
        chunks, memories = await asyncio.to_thread(
            run_multi_step_retrieval, ctx.client, ctx.embedder, ctx.llm, uid, query, RAG_TOP_K, RAG_MIN_SIMILARITY
        )
        chunks = await crag_correct(ctx.llm, ctx.web_search_fn, query, chunks)
    else:  # SINGLE
        hyde_query = await asyncio.to_thread(generate_hypothetical_answer, ctx.llm, query)
        chunks = await asyncio.to_thread(
            retrieve_chunks, ctx.client, ctx.embedder, uid, hyde_query, RAG_TOP_K, RAG_MIN_SIMILARITY
        )
        chunks = await crag_correct(ctx.llm, ctx.web_search_fn, query, chunks)
        memories = await asyncio.to_thread(
            retrieve_memories, ctx.client, ctx.embedder, uid, query, MEMORY_TOP_K, MEMORY_MIN_SIMILARITY
        )

    apply_self_consistency(memories, query)
    profile = await asyncio.to_thread(get_or_create_profile, ctx.client, uid)

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
    mcp = FastMCP("hoton-graphtr", stateless_http=True, json_response=True)

    @mcp.tool()
    async def get_rag_context(user_id: str, query: str) -> RagContextResult:
        """Retrieve merged profile/memory/knowledge context for a chat turn."""
        with track_usage(ctx.usage_store, "get_rag_context", user_id):
            return await get_rag_context_impl(ctx, user_id, query)

    @mcp.tool()
    def retrieve_chunks(user_id: str, query: str, top_k: int = 5, min_similarity: float = 0.0) -> RetrieveChunksResult:
        """Raw chunk search, for workflow rag_query nodes."""
        with track_usage(ctx.usage_store, "retrieve_chunks", user_id):
            return retrieve_chunks_impl(ctx, user_id, query, top_k, min_similarity)

    @mcp.tool()
    def extract_and_store_memories(user_id: str, user_message: str, assistant_message: str) -> ExtractMemoriesResult:
        """Post-turn hook: extract and store facts/preferences from a conversation turn."""
        with track_usage(ctx.usage_store, "extract_and_store_memories", user_id):
            return extract_and_store_memories_impl(ctx, user_id, user_message, assistant_message)

    @mcp.tool()
    def update_profile_from_message(user_id: str, user_message: str) -> UpdateProfileResult:
        """Post-turn hook: update the user's profile signals from their message."""
        with track_usage(ctx.usage_store, "update_profile_from_message", user_id):
            return update_profile_from_message_impl(ctx, user_id, user_message)

    @mcp.tool()
    def embed_text(text: str) -> EmbedTextResult:
        """Raw embedding, for workflow embedding nodes."""
        with track_usage(ctx.usage_store, "embed_text", ""):
            return embed_text_impl(ctx, text)

    @mcp.tool()
    def ingest_codebase(source: str) -> IngestCodebaseResult:
        """Parse a local repo path into <repo>/graphtr-out/ (graph.json, manifest.json,
        graphtr.html). One-shot: nothing is kept server-side, query the output offline
        with scripts/query.py. Git URLs are not supported -- clone first, pass a path."""
        with track_usage(ctx.usage_store, "ingest_codebase", ""):
            return ingest_codebase_impl(source)

    return mcp
