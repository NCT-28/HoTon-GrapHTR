"""Chunk retrieval — ported from hoton-lmr/src/rag/retrieval.rs::retrieve_chunks."""

import datetime
import uuid
from dataclasses import dataclass

from qdrant_client import QdrantClient
from qdrant_client.models import FieldCondition, Filter, MatchValue

from app.clients.qdrant_store import RAG_CHUNKS


@dataclass
class RetrievedChunk:
    id: str
    content: str
    document_title: str | None
    source_url: str | None
    similarity: float
    document_expired: bool


def _is_expired(valid_until: str | None) -> bool:
    if not valid_until:
        return False
    return datetime.datetime.fromisoformat(valid_until) < datetime.datetime.utcnow()


def retrieve_chunks(
    client: QdrantClient,
    embedder,
    user_id: uuid.UUID,
    query: str,
    top_k: int,
    min_similarity: float,
) -> list[RetrievedChunk]:
    query_vector = embedder.embed_single(query)

    hits = client.query_points(
        collection_name=RAG_CHUNKS,
        query=query_vector,
        query_filter=Filter(must=[FieldCondition(key="user_id", match=MatchValue(value=str(user_id)))]),
        limit=top_k * 3,  # over-fetch: some will be dropped by the expiry discount below
        with_payload=True,
    ).points

    results: list[RetrievedChunk] = []
    for hit in hits:
        payload = hit.payload
        expired = _is_expired(payload.get("valid_until"))
        similarity = hit.score * 0.6 if expired else hit.score
        if similarity < min_similarity:
            continue
        results.append(
            RetrievedChunk(
                id=str(hit.id),
                content=payload["content"],
                document_title=payload.get("document_title"),
                source_url=payload.get("source_url"),
                similarity=similarity,
                document_expired=expired,
            )
        )

    results.sort(key=lambda r: r.similarity, reverse=True)
    return results[:top_k]
