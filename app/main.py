from fastapi import FastAPI

from app.documents import build_documents_router
from app.embeddings import get_embedder
from app.qdrant_store import get_qdrant_client


def create_app(qdrant_client=None, embedder=None) -> FastAPI:
    app = FastAPI(title="hoton-rag")

    get_client_fn = (lambda: qdrant_client) if qdrant_client is not None else get_qdrant_client
    get_embedder_fn = (lambda: embedder) if embedder is not None else get_embedder

    app.include_router(build_documents_router(get_client_fn, get_embedder_fn))

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app


app = create_app()
