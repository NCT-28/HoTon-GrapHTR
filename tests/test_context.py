import pytest

from app.context import apply_self_consistency, build_full_rag_context
from app.memory import RetrievedMemory
from app.retrieval import RetrievedChunk


def test_apply_self_consistency_penalizes_short_preference_on_long_query():
    memories = [RetrievedMemory(id="1", content="User prefers short, concise answers", memory_type="preference", confidence=0.8)]
    long_query = "x" * 301
    apply_self_consistency(memories, long_query)
    assert memories[0].confidence == pytest.approx(0.8 * 0.6)


def test_apply_self_consistency_drops_below_floor():
    memories = [RetrievedMemory(id="1", content="User prefers concise answers", memory_type="preference", confidence=0.4)]
    apply_self_consistency(memories, "x" * 301)  # 0.4 * 0.6 = 0.24, below 0.3 floor
    assert memories == []


def test_apply_self_consistency_no_adjustment_for_unrelated_memory():
    memories = [RetrievedMemory(id="1", content="User is a backend engineer", memory_type="fact", confidence=0.8)]
    apply_self_consistency(memories, "x" * 301)
    assert memories[0].confidence == 0.8


def test_build_full_rag_context_empty_returns_empty_string():
    assert build_full_rag_context([], []) == ""


def test_build_full_rag_context_combines_memory_and_chunks():
    chunks = [RetrievedChunk(id="c1", content="Chunk body", document_title="Doc A", source_url="https://x.com", similarity=0.9, document_expired=False)]
    memories = [RetrievedMemory(id="m1", content="User likes Rust", memory_type="fact", confidence=0.7)]

    result = build_full_rag_context(chunks, memories)

    assert "[What I know about you]" in result
    assert "- User likes Rust" in result
    assert "[Knowledge Context]" in result
    assert "Doc A (https://x.com)" in result
    assert "Chunk body" in result
    assert result.index("[What I know about you]") < result.index("[Knowledge Context]")
