from fastapi import FastAPI

from app.browser_client import BrowserClient
from app.config import get_settings
from app.documents import add_url_route, build_documents_router
from app.embeddings import get_embedder
from app.qdrant_store import get_qdrant_client


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

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app


app = create_app()
