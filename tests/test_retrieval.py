import datetime
import uuid

from qdrant_client.models import PointStruct

from app.clients.qdrant_store import RAG_CHUNKS
from app.rag.retrieval import retrieve_chunks


class FixedEmbedder:
    def __init__(self, vector):
        self._vector = vector

    def embed_single(self, text):
        return self._vector


def _insert_chunk(qdrant, user_id, content, vector, document_id="doc-1", valid_until=None):
    qdrant.upsert(
        collection_name=RAG_CHUNKS,
        points=[
            PointStruct(
                id=str(uuid.uuid4()),
                vector=vector,
                payload={
                    "user_id": str(user_id),
                    "document_id": document_id,
                    "content": content,
                    "document_title": "Doc",
                    "source_url": None,
                    "valid_until": valid_until,
                },
            )
        ],
        wait=True,
    )


def test_retrieve_chunks_filters_by_user_and_similarity(qdrant):
    user_id = uuid.uuid4()
    other_user = uuid.uuid4()
    query_vec = [1.0] + [0.0] * 383

    _insert_chunk(qdrant, user_id, "matching chunk", query_vec)
    _insert_chunk(qdrant, other_user, "other user's chunk", query_vec)
    _insert_chunk(qdrant, user_id, "unrelated chunk", [0.0, 1.0] + [0.0] * 382)

    results = retrieve_chunks(
        qdrant, FixedEmbedder(query_vec), user_id, "query", top_k=5, min_similarity=0.5
    )

    assert len(results) == 1
    assert results[0].content == "matching chunk"


def test_retrieve_chunks_discounts_expired_documents(qdrant):
    user_id = uuid.uuid4()
    query_vec = [1.0] + [0.0] * 383
    past = (datetime.datetime.utcnow() - datetime.timedelta(days=1)).isoformat()

    _insert_chunk(qdrant, user_id, "expired chunk", query_vec, valid_until=past)

    results = retrieve_chunks(
        qdrant, FixedEmbedder(query_vec), user_id, "query", top_k=5, min_similarity=0.9
    )

    # similarity ~1.0 * 0.6 = 0.6, below the 0.9 threshold after discount
    assert results == []

    results_lower_threshold = retrieve_chunks(
        qdrant, FixedEmbedder(query_vec), user_id, "query", top_k=5, min_similarity=0.5
    )
    assert len(results_lower_threshold) == 1
    assert results_lower_threshold[0].document_expired is True
    assert results_lower_threshold[0].similarity < 0.9
