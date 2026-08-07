"""Dependency-light MCP server exposing only `ingest_codebase`, for
machines that don't run (or can't install) the RAG stack: no torch,
sentence-transformers, qdrant-client, neo4j, or psycopg required. See
docs/superpowers/specs/2026-08-06-graph-only-mcp-server-design.md."""
import contextlib

from fastapi import FastAPI
from mcp.server.fastmcp import FastMCP

from app.graph.ingest import IngestCodebaseResult, ingest_codebase_impl


def create_graph_only_app() -> FastAPI:
    mcp = FastMCP("hoton-graphtr-graph-only", stateless_http=True, json_response=True)

    @mcp.tool()
    def ingest_codebase(source: str) -> IngestCodebaseResult:
        """Parse a local repo path into <repo>/graphtr-out/ (graph.json, manifest.json,
        graphtr.html). One-shot: nothing is kept server-side, query the output offline
        with scripts/query.py. Git URLs are not supported -- clone first, pass a path."""
        return ingest_codebase_impl(source)

    mcp_app = mcp.streamable_http_app()  # must be called once before mcp.session_manager exists

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        async with mcp.session_manager.run():
            yield

    app = FastAPI(title="hoton-graphtr-graph-only", lifespan=lifespan)

    @app.get("/health")
    async def health():
        return {"status": "ok", "mode": "graph-only"}

    # mcp_app already owns the "/mcp" path internally (FastMCP's streamable_http_path
    # default) -- mounting it at "/mcp" here would double the prefix to "/mcp/mcp".
    app.mount("/", mcp_app)

    return app
