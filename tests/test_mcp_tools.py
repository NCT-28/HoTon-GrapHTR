import uuid

import pytest
from qdrant_client.models import PointStruct

from app.mcp_server import (
    EmbedTextResult,
    RagContextResult,
    RetrieveChunksResult,
    UpdateProfileResult,
    build_tool_context,
    embed_text_impl,
    get_rag_context_impl,
    retrieve_chunks_impl,
    update_profile_from_message_impl,
)
from app.clients.qdrant_store import RAG_CHUNKS


class FakeEmbedder:
    dim = 384

    def embed_single(self, text):
        return [1.0] + [0.0] * 383

    def embed_batch(self, texts):
        return [[1.0] + [0.0] * 383 for _ in texts]


def _ctx(qdrant):
    return build_tool_context(qdrant, FakeEmbedder())


@pytest.mark.asyncio
async def test_get_rag_context_retrieves_chunks_via_vector_search(qdrant):
    user_id = uuid.uuid4()
    qdrant.upsert(
        collection_name=RAG_CHUNKS,
        points=[
            PointStruct(
                id=str(uuid.uuid4()),
                vector=[1.0] + [0.0] * 383,
                payload={
                    "user_id": str(user_id), "document_id": "d1", "content": "Relevant chunk content",
                    "document_title": "Doc", "source_url": None, "valid_until": None,
                },
            )
        ],
        wait=True,
    )
    ctx = _ctx(qdrant)
    result = await get_rag_context_impl(ctx, str(user_id), "query")
    assert isinstance(result, RagContextResult)
    assert result.chunks_used == 1
    assert "Relevant chunk content" in result.context_text
    assert len(result.chunks) == 1
    assert result.chunks[0].document_title == "Doc"


@pytest.mark.asyncio
async def test_get_rag_context_empty_when_nothing_indexed(qdrant):
    result = await get_rag_context_impl(_ctx(qdrant), str(uuid.uuid4()), "query")
    assert result.chunks_used == 0
    assert result.memories_used == 0


@pytest.mark.asyncio
async def test_get_rag_context_takes_no_repo_id(qdrant):
    import inspect

    params = list(inspect.signature(get_rag_context_impl).parameters)
    assert params == ["ctx", "user_id", "query"]

    result = await get_rag_context_impl(_ctx(qdrant), str(uuid.uuid4()), "how does chunk retrieval work?")

    assert "[Code Graph Context]" not in result.context_text


def test_retrieve_chunks_impl_returns_list(qdrant):
    ctx = _ctx(qdrant)
    result = retrieve_chunks_impl(ctx, str(uuid.uuid4()), "query", top_k=5, min_similarity=0.5)
    assert isinstance(result, RetrieveChunksResult)
    assert result.chunks == []


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
