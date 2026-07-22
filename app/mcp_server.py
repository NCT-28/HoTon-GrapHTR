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
from app.code_graph_store import GraphStore
from app.graph_query import bfs_query, explain_node, shortest_path
from app.repo_source import resolve_repo_source
from app.repo_watcher import RepoWatcherManager


@dataclass
class ToolContext:
    client: QdrantClient
    embedder: object
    llm: object
    web_search_fn: object  # Callable[[str], Awaitable[list[str]]]
    graph_store: GraphStore | None = None
    watcher_manager: RepoWatcherManager | None = None


def build_tool_context(
    client: QdrantClient, embedder, llm, web_search_fn,
    graph_store: GraphStore | None = None, watcher_manager: RepoWatcherManager | None = None,
) -> ToolContext:
    return ToolContext(
        client=client, embedder=embedder, llm=llm, web_search_fn=web_search_fn,
        graph_store=graph_store, watcher_manager=watcher_manager,
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


class IngestCodebaseResult(BaseModel):
    repo_id: str
    symbol_count: int
    edge_count: int


class GraphNodeOut(BaseModel):
    id: str
    name: str
    kind: str | None = None
    file_path: str | None = None
    start_line: int | None = None
    end_line: int | None = None


class GraphEdgeOut(BaseModel):
    source: str
    target: str
    type: str


class QueryCodeGraphResult(BaseModel):
    nodes: list[GraphNodeOut]
    edges: list[GraphEdgeOut]


def _to_node_out(node: dict) -> GraphNodeOut:
    return GraphNodeOut(
        id=node["id"], name=node["name"], kind=node.get("kind"), file_path=node.get("file_path"),
        start_line=node.get("start_line"), end_line=node.get("end_line"),
    )


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


def ingest_codebase_impl(ctx: ToolContext, user_id: str, source: str) -> IngestCodebaseResult:
    repo_id = str(uuid.uuid4())
    local_path = resolve_repo_source(source, repo_id)
    ctx.watcher_manager.reindex(user_id, repo_id, local_path)
    ctx.watcher_manager.watch(user_id, repo_id, local_path)
    nodes, edges = ctx.graph_store.get_subgraph(user_id, repo_id)
    return IngestCodebaseResult(repo_id=repo_id, symbol_count=len(nodes), edge_count=len(edges))


def query_code_graph_impl(
    ctx: ToolContext, user_id: str, repo_id: str, mode: str,
    keyword: str = "", from_name: str = "", to_name: str = "", name: str = "", depth: int = 2,
) -> QueryCodeGraphResult:
    nodes, edges = ctx.graph_store.get_subgraph(user_id, repo_id)

    if mode == "query":
        result_nodes, result_edges = bfs_query(nodes, edges, keyword, depth)
    elif mode == "path":
        result = shortest_path(nodes, edges, from_name, to_name)
        result_nodes, result_edges = result if result else ([], [])
    elif mode == "explain":
        result = explain_node(nodes, edges, name)
        if result is None:
            result_nodes, result_edges = [], []
        else:
            center, neighbors, related_edges = result
            result_nodes, result_edges = [center, *neighbors], related_edges
    else:
        raise ValueError(f"unknown query_code_graph mode: {mode}")

    return QueryCodeGraphResult(
        nodes=[_to_node_out(n) for n in result_nodes],
        edges=[GraphEdgeOut(source=e["source"], target=e["target"], type=e["type"]) for e in result_edges],
    )


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

    @mcp.tool()
    def ingest_codebase(user_id: str, source: str) -> IngestCodebaseResult:
        """Parse a local path or git URL into the code knowledge graph and start watching it for changes."""
        return ingest_codebase_impl(ctx, user_id, source)

    @mcp.tool()
    def query_code_graph(
        user_id: str, repo_id: str, mode: str,
        keyword: str = "", from_name: str = "", to_name: str = "", name: str = "", depth: int = 2,
    ) -> QueryCodeGraphResult:
        """Query the code graph for a repo. mode='query' (keyword BFS from `keyword`),
        'path' (shortest path from `from_name` to `to_name`), 'explain' (node + neighbors by `name`)."""
        return query_code_graph_impl(ctx, user_id, repo_id, mode, keyword, from_name, to_name, name, depth)

    return mcp
