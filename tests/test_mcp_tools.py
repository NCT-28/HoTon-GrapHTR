import uuid

from app.mcp_server import (
    EmbedTextResult,
    ExtractMemoriesResult,
    RagContextResult,
    RetrieveChunksResult,
    UpdateProfileResult,
    build_tool_context,
    embed_text_impl,
    extract_and_store_memories_impl,
    get_rag_context_impl,
    retrieve_chunks_impl,
    update_profile_from_message_impl,
)
from app.qdrant_store import RAG_CHUNKS, USER_MEMORIES
from qdrant_client.models import PointStruct


class FakeEmbedder:
    dim = 384

    def embed_single(self, text):
        return [1.0] + [0.0] * 383

    def embed_batch(self, texts):
        return [[1.0] + [0.0] * 383 for _ in texts]


class FakeLLM:
    def __init__(self, response_text="[]"):
        self._response_text = response_text

    def generate(self, prompt, max_new_tokens=256, temperature=0.1):
        return self._response_text


def _ctx(qdrant):
    return build_tool_context(qdrant, FakeEmbedder(), FakeLLM())


def test_get_rag_context_empty_for_new_user(qdrant):
    ctx = _ctx(qdrant)
    result = get_rag_context_impl(ctx, str(uuid.uuid4()), "hello")
    assert isinstance(result, RagContextResult)
    assert result.context_text == ""
    assert result.chunks_used == 0
    assert result.memories_used == 0


def test_get_rag_context_includes_matching_chunk(qdrant):
    user_id = uuid.uuid4()
    qdrant.upsert(
        collection_name=RAG_CHUNKS,
        points=[
            PointStruct(
                id=str(uuid.uuid4()),
                vector=[1.0] + [0.0] * 383,
                payload={
                    "user_id": str(user_id),
                    "document_id": "doc-1",
                    "content": "Relevant chunk content",
                    "document_title": "Doc",
                    "source_url": None,
                    "valid_until": None,
                },
            )
        ],
        wait=True,
    )

    ctx = _ctx(qdrant)
    result = get_rag_context_impl(ctx, str(user_id), "query")
    assert result.chunks_used == 1
    assert "Relevant chunk content" in result.context_text


def test_retrieve_chunks_impl_returns_list(qdrant):
    ctx = _ctx(qdrant)
    result = retrieve_chunks_impl(ctx, str(uuid.uuid4()), "query", top_k=5, min_similarity=0.5)
    assert isinstance(result, RetrieveChunksResult)
    assert result.chunks == []


def test_extract_and_store_memories_impl(qdrant):
    ctx = build_tool_context(qdrant, FakeEmbedder(), FakeLLM('[{"content": "User likes tea", "type": "fact", "confidence": 0.6}]'))
    result = extract_and_store_memories_impl(ctx, str(uuid.uuid4()), "I like tea", "noted")
    assert isinstance(result, ExtractMemoriesResult)
    assert result.stored == 1


def test_update_profile_from_message_impl(qdrant):
    ctx = _ctx(qdrant)
    user_id = str(uuid.uuid4())
    long_msg = "Can you help me refactor this function and debug the API cache and database query performance issue in the container?"
    result = update_profile_from_message_impl(ctx, user_id, long_msg)
    assert isinstance(result, UpdateProfileResult)
    assert result.updated is True


def test_embed_text_impl_returns_vector(qdrant):
    ctx = _ctx(qdrant)
    result = embed_text_impl(ctx, "some text")
    assert isinstance(result, EmbedTextResult)
    assert len(result.embedding) == 384
