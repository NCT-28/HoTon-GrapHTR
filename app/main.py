from fastapi import FastAPI, Header

from app.browser_client import BrowserClient
from app.config import get_settings
from app.documents import add_url_route, build_documents_router
from app.embeddings import get_embedder
from app.memory import build_memory_router
from app.profile import build_profile_router
from app.qdrant_store import RAG_DOCUMENTS, get_qdrant_client


def create_app(qdrant_client=None, embedder=None, browser_client=None) -> FastAPI:
    app = FastAPI(title="hoton-rag")

    get_client_fn = (lambda: qdrant_client) if qdrant_client is not None else get_qdrant_client
    get_embedder_fn = (lambda: embedder) if embedder is not None else get_embedder
    get_browser_fn = (lambda: browser_client) if browser_client is not None else (
        lambda: BrowserClient(get_settings().browser_service_url)
    )

    router = build_documents_router(get_client_fn, get_embedder_fn)
    add_url_route(router, get_client_fn, get_embedder_fn, get_browser_fn)
    app.include_router(router)
    app.include_router(build_memory_router(get_client_fn))
    app.include_router(build_profile_router(get_client_fn))

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/api/status")
    async def rag_status(x_user_id: str = Header(...)):
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        from app.qdrant_store import USER_MEMORIES

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

    return app


app = create_app()
