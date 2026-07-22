from functools import lru_cache

from qdrant_client import QdrantClient
from qdrant_client.http.exceptions import UnexpectedResponse
from qdrant_client.models import Distance, VectorParams

from app.config import get_settings

RAG_DOCUMENTS = "rag_documents"
RAG_CHUNKS = "rag_chunks"
USER_MEMORIES = "user_memories"
USER_PROFILES = "user_profiles"
PROFILE_SNAPSHOTS = "profile_snapshots"
CODE_SYMBOL_EMBEDDINGS = "code_symbol_embeddings"


def bootstrap_collections(client: QdrantClient, embed_dim: int) -> None:
    """Create the three collections if they don't already exist. Safe to call repeatedly."""
    existing = {c.name for c in client.get_collections().collections}

    # rag_documents holds metadata only — looked up by id, never vector-searched.
    # Qdrant requires a vector config per collection, so we give it a 1-dim
    # placeholder vector that is never queried against.
    if RAG_DOCUMENTS not in existing:
        client.create_collection(
            collection_name=RAG_DOCUMENTS,
            vectors_config=VectorParams(size=1, distance=Distance.COSINE),
        )

    if RAG_CHUNKS not in existing:
        client.create_collection(
            collection_name=RAG_CHUNKS,
            vectors_config=VectorParams(size=embed_dim, distance=Distance.COSINE),
        )

    if USER_MEMORIES not in existing:
        client.create_collection(
            collection_name=USER_MEMORIES,
            vectors_config=VectorParams(size=embed_dim, distance=Distance.COSINE),
        )

    if USER_PROFILES not in existing:
        client.create_collection(
            collection_name=USER_PROFILES,
            vectors_config=VectorParams(size=1, distance=Distance.COSINE),
        )

    if PROFILE_SNAPSHOTS not in existing:
        client.create_collection(
            collection_name=PROFILE_SNAPSHOTS,
            vectors_config=VectorParams(size=1, distance=Distance.COSINE),
        )

    if CODE_SYMBOL_EMBEDDINGS not in existing:
        client.create_collection(
            collection_name=CODE_SYMBOL_EMBEDDINGS,
            vectors_config=VectorParams(size=embed_dim, distance=Distance.COSINE),
        )


@lru_cache
def get_qdrant_client() -> QdrantClient:
    settings = get_settings()
    client = QdrantClient(url=settings.qdrant_url)
    bootstrap_collections(client, embed_dim=settings.embed_dim)
    return client
