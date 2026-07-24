import pytest

from app.rag.context import apply_self_consistency, build_full_rag_context
from app.rag.memory import RetrievedMemory
from app.rag.retrieval import RetrievedChunk


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


def test_profile_section_empty_for_default_profile():
    from app.rag.context import build_profile_context_section
    from app.rag.profile import UserProfile

    assert build_profile_context_section(UserProfile()) == ""


def test_profile_section_renders_non_default_fields():
    from app.rag.context import build_profile_context_section
    from app.rag.profile import UserProfile

    profile = UserProfile(level="advanced", style="terse", preferred_lang="vi", project_context="a RAG service")
    section = build_profile_context_section(profile)
    assert "[User Context]" in section
    assert "Expertise level: advanced" in section
    assert "Communication style: terse" in section
    assert "Language: vi" in section
    assert "Current project: a RAG service" in section


def test_build_full_context_orders_profile_memory_rag():
    from app.rag.context import build_full_context
    from app.rag.profile import UserProfile

    profile = UserProfile(level="advanced")
    memories = [RetrievedMemory(id="m1", content="User likes Rust", memory_type="fact", confidence=0.7)]
    chunks = [RetrievedChunk(id="c1", content="Chunk body", document_title="Doc A", source_url=None, similarity=0.9, document_expired=False)]

    result = build_full_context(chunks, memories, profile)

    assert result.index("[User Context]") < result.index("[What I know about you]") < result.index("[Knowledge Context]")


def test_build_full_context_all_empty_returns_empty_string():
    from app.rag.context import build_full_context
    from app.rag.profile import UserProfile

    assert build_full_context([], [], UserProfile()) == ""


def test_build_graph_context_section_empty_returns_empty_string():
    from app.rag.context import build_graph_context_section

    assert build_graph_context_section([], []) == ""


def test_build_graph_context_section_renders_nodes_and_edges():
    from app.rag.context import build_graph_context_section

    nodes = [
        {"id": "1", "name": "retrieve_chunks", "kind": "function", "file_path": "app/rag/retrieval.py", "start_line": 29},
        {"id": "2", "name": "embed_single", "kind": "method"},
    ]
    edges = [{"source": "1", "target": "2", "type": "CALLS"}]

    section = build_graph_context_section(nodes, edges)

    assert "[Code Graph Context]" in section
    assert "retrieve_chunks (function) — app/rag/retrieval.py:29" in section
    assert "--CALLS--> embed_single" in section


def test_build_full_context_appends_graph_section_after_rag_context():
    from app.rag.context import build_full_context
    from app.rag.profile import UserProfile

    chunks = [RetrievedChunk(id="c1", content="Chunk body", document_title="Doc A", source_url=None, similarity=0.9, document_expired=False)]
    graph_nodes = [{"id": "1", "name": "foo", "kind": "function"}]

    result = build_full_context(chunks, [], UserProfile(), graph_nodes=graph_nodes, graph_edges=[])

    assert "[Code Graph Context]" in result
    assert result.index("[Knowledge Context]") < result.index("[Code Graph Context]")


def test_build_full_context_no_graph_args_unchanged():
    from app.rag.context import build_full_context
    from app.rag.profile import UserProfile

    assert build_full_context([], [], UserProfile()) == ""
