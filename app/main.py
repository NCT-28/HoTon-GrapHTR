import asyncio
import contextlib

from fastapi import FastAPI, Header

from app.clients.browser_client import BrowserClient
from app.rag.cleanup import start_memory_cleanup_job
from app.config import get_settings
from app.rag.documents import add_url_route, build_documents_router
from app.clients.embeddings import get_embedder
from app.mcp_server import build_mcp_server, build_tool_context
from app.rag.memory import build_memory_router
from app.rag.profile import build_profile_router
from app.clients.qdrant_store import RAG_DOCUMENTS, USER_MEMORIES, get_qdrant_client
from app.dashboard.router import build_dashboard_router
from app.dashboard.usage_store import get_usage_store


def create_app(
    qdrant_client=None, embedder=None, browser_client=None, usage_store=None,
) -> FastAPI:
    get_client_fn = (lambda: qdrant_client) if qdrant_client is not None else get_qdrant_client
    get_embedder_fn = (lambda: embedder) if embedder is not None else get_embedder
    get_browser_fn = (lambda: browser_client) if browser_client is not None else (
        lambda: BrowserClient(get_settings().browser_service_url)
    )
    get_usage_store_fn = (lambda: usage_store) if usage_store is not None else get_usage_store

    tool_ctx = build_tool_context(get_client_fn(), get_embedder_fn(), get_usage_store_fn())
    mcp = build_mcp_server(tool_ctx)
    mcp_app = mcp.streamable_http_app()  # must be called once before mcp.session_manager exists

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        cleanup_task = asyncio.create_task(start_memory_cleanup_job(get_client_fn()))
        async with mcp.session_manager.run():
            yield
        cleanup_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await cleanup_task

    app = FastAPI(title="hoton-graphtr", lifespan=lifespan)

    router = build_documents_router(get_client_fn, get_embedder_fn, get_usage_store_fn)
    add_url_route(router, get_client_fn, get_embedder_fn, get_browser_fn, get_usage_store_fn)
    app.include_router(router)
    app.include_router(build_memory_router(get_client_fn, get_usage_store_fn))
    app.include_router(build_profile_router(get_client_fn, get_usage_store_fn))
    app.include_router(build_dashboard_router(get_client_fn, get_usage_store_fn, get_embedder_fn))

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/api/status")
    async def rag_status(x_user_id: str = Header(...)):
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        client = get_client_fn()
        doc_count = client.count(
            collection_name=RAG_DOCUMENTS,
            count_filter=Filter(must=[FieldCondition(key="user_id", match=MatchValue(value=x_user_id))]),
        ).count
        memory_count = client.count(
            collection_name=USER_MEMORIES,
            count_filter=Filter(must=[FieldCondition(key="user_id", match=MatchValue(value=x_user_id))]),
        ).count
        return {"embed_model_loaded": True, "doc_count": doc_count, "memory_count": memory_count}

    # mcp_app already owns the "/mcp" path internally (FastMCP's streamable_http_path
    # default) — mounting it at "/mcp" here would double the prefix to "/mcp/mcp".
    app.mount("/", mcp_app)

    return app
