import os
import uuid
from functools import lru_cache

from qdrant_client import QdrantClient
from qdrant_client.http.exceptions import UnexpectedResponse
from qdrant_client.models import Distance, FieldCondition, Filter, MatchValue, VectorParams

from app.config import get_settings

RAG_DOCUMENTS = "rag_documents"
RAG_CHUNKS = "rag_chunks"
USER_MEMORIES = "user_memories"
USER_PROFILES = "user_profiles"
PROFILE_SNAPSHOTS = "profile_snapshots"
CODE_SYMBOL_EMBEDDINGS = "code_symbol_embeddings"

# Fixed, arbitrary namespace for deriving Qdrant point ids from stable symbol ids
# (see code_parser.py's _symbol_id) via uuid5 — Qdrant only accepts u64 ints or
# UUIDs as point ids (confirmed against Qdrant docs), not arbitrary strings, so
# the symbol's own sha1-hex id can't be used directly.
_SYMBOL_POINT_NAMESPACE = uuid.UUID("6f6e6f74-6f68-7274-7267-617068747200")


def symbol_point_id(symbol_id: str) -> str:
    return str(uuid.uuid5(_SYMBOL_POINT_NAMESPACE, symbol_id))


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
    if settings.deploy_mode == "local":
        os.makedirs(settings.local_data_dir, exist_ok=True)
        client = QdrantClient(path=os.path.join(settings.local_data_dir, "qdrant"))
    else:
        client = QdrantClient(url=settings.qdrant_url)
    bootstrap_collections(client, embed_dim=settings.embed_dim)
    return client


def count_code_symbol_embeddings(client, graph_store, user_id: str, repo_id: str) -> int | None:
    """Vector count for one repo's code symbols, regardless of deploy mode. DEPLOY_MODE=local
    keeps them in a per-repo embedded Qdrant (see get_repo_qdrant_client) instead of `client`,
    so this resolves and counts that one directly rather than filtering `client`, which server
    mode's single shared collection counts by user_id/repo_id payload instead."""
    settings = get_settings()
    if settings.deploy_mode == "local":
        repo = graph_store.get_repo(user_id, repo_id) if graph_store is not None else None
        local_path = repo.get("local_path") if repo else None
        if not local_path or not os.path.isdir(local_path):
            return None
        try:
            return get_repo_qdrant_client(local_path).count(collection_name=CODE_SYMBOL_EMBEDDINGS).count
        except Exception:
            return None

    if client is None:
        return None
    try:
        return client.count(
            collection_name=CODE_SYMBOL_EMBEDDINGS,
            count_filter=Filter(must=[
                FieldCondition(key="user_id", match=MatchValue(value=user_id)),
                FieldCondition(key="repo_id", match=MatchValue(value=repo_id)),
            ]),
        ).count
    except Exception:
        return None


@lru_cache
def get_repo_qdrant_client(local_path: str) -> QdrantClient:
    """Per-repo embedded Qdrant instance for DEPLOY_MODE=local, so a repo's code-symbol
    vectors live under <local_path>/graphtr-out/qdrant instead of one collection shared
    (and growing unbounded) across every locally-ingested repo."""
    settings = get_settings()
    repo_data_dir = os.path.join(local_path, "graphtr-out")
    os.makedirs(repo_data_dir, exist_ok=True)
    client = QdrantClient(path=os.path.join(repo_data_dir, "qdrant"))
    bootstrap_collections(client, embed_dim=settings.embed_dim)
    return client
