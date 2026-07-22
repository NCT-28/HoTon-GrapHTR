import uuid

import pytest
from qdrant_client.models import PointStruct

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
from app.clients.qdrant_store import RAG_CHUNKS


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


class ScriptedLLM:
    """Routes canned responses by inspecting distinguishing text in each prompt,
    since the full pipeline calls generate() for routing, HyDE, grading, and continue-decisions
    in sequence with different prompts."""

    def __init__(self, *, classification="single", hyde_text="hypothetical passage", grade="0.9", continue_json='{"enough": true, "next_query": null}', extraction="[]"):
        self.classification = classification
        self.hyde_text = hyde_text
        self.grade = grade
        self.continue_json = continue_json
        self.extraction = extraction

    def generate(self, prompt, max_new_tokens=256, temperature=0.1):
        if "Classification:" in prompt:
            return self.classification
        if "Passage:" in prompt and "hypothetical" in prompt.lower():
            return self.hyde_text
        if "Relevance score:" in prompt:
            return self.grade
        if "JSON:" in prompt and "enough" in prompt:
            return self.continue_json
        return self.extraction


async def _fake_web_search(query):
    return []


def _ctx(qdrant, **llm_kwargs):
    if llm_kwargs:
        return build_tool_context(qdrant, FakeEmbedder(), ScriptedLLM(**llm_kwargs), _fake_web_search)
    return build_tool_context(qdrant, FakeEmbedder(), FakeLLM(), _fake_web_search)


@pytest.mark.asyncio
async def test_get_rag_context_direct_skips_retrieval_entirely(qdrant):
    ctx = _ctx(qdrant, classification="direct")
    result = await get_rag_context_impl(ctx, str(uuid.uuid4()), "hi there")
    assert isinstance(result, RagContextResult)
    assert result.context_text == ""
    assert result.chunks_used == 0
    assert result.memories_used == 0


@pytest.mark.asyncio
async def test_get_rag_context_single_retrieves_via_hyde(qdrant):
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
    ctx = _ctx(qdrant, classification="single", grade="0.9")
    result = await get_rag_context_impl(ctx, str(user_id), "query")
    assert result.chunks_used == 1
    assert "Relevant chunk content" in result.context_text
    assert len(result.chunks) == 1
    assert result.chunks[0].document_title == "Doc"


@pytest.mark.asyncio
async def test_get_rag_context_single_falls_back_to_web_on_low_relevance(qdrant):
    user_id = uuid.uuid4()
    qdrant.upsert(
        collection_name=RAG_CHUNKS,
        points=[
            PointStruct(
                id=str(uuid.uuid4()),
                vector=[1.0] + [0.0] * 383,
                payload={
                    "user_id": str(user_id), "document_id": "d1", "content": "irrelevant chunk",
                    "document_title": "Doc", "source_url": None, "valid_until": None,
                },
            )
        ],
        wait=True,
    )

    async def fake_web_search(query):
        return ["web snippet content"]

    ctx = build_tool_context(qdrant, FakeEmbedder(), ScriptedLLM(classification="single", grade="0.1"), fake_web_search)
    result = await get_rag_context_impl(ctx, str(user_id), "query")
    assert "web snippet content" in result.context_text
    assert result.chunks_used == 2


@pytest.mark.asyncio
async def test_get_rag_context_multi_uses_react_loop(qdrant):
    user_id = uuid.uuid4()
    qdrant.upsert(
        collection_name=RAG_CHUNKS,
        points=[
            PointStruct(
                id=str(uuid.uuid4()),
                vector=[1.0] + [0.0] * 383,
                payload={
                    "user_id": str(user_id), "document_id": "d1", "content": "multi-step chunk",
                    "document_title": "Doc", "source_url": None, "valid_until": None,
                },
            )
        ],
        wait=True,
    )
    ctx = _ctx(qdrant, classification="multi", grade="0.9", continue_json='{"enough": true, "next_query": null}')
    result = await get_rag_context_impl(ctx, str(user_id), "complex multi-part question")
    assert result.chunks_used == 1
    assert "multi-step chunk" in result.context_text


def test_retrieve_chunks_impl_returns_list(qdrant):
    ctx = _ctx(qdrant)
    result = retrieve_chunks_impl(ctx, str(uuid.uuid4()), "query", top_k=5, min_similarity=0.5)
    assert isinstance(result, RetrieveChunksResult)
    assert result.chunks == []


def test_extract_and_store_memories_impl(qdrant):
    ctx = build_tool_context(
        qdrant, FakeEmbedder(),
        FakeLLM('[{"content": "User likes tea", "type": "fact", "confidence": 0.6}]'),
        _fake_web_search,
    )
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
