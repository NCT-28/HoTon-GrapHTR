from app.graph.entity_linker import link_entities_to_code


class _FakeEmbedder:
    """Deterministic: embeds a string to a 1-dim vector of its length, so
    similarity ranking is predictable in tests."""

    def embed_single(self, text: str) -> list[float]:
        return [float(len(text))]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_single(t) for t in texts]


class _FakeLLM:
    def __init__(self, confirm_names: set[str]):
        self._confirm_names = confirm_names

    def generate(self, prompt: str, max_new_tokens: int = 5, temperature: float = 0.0) -> str:
        # Match on the symbol-name slot specifically (not entity-name, which
        # appears in every prompt regardless of which candidate is being checked).
        for name in self._confirm_names:
            if f'code symbol "{name}"' in prompt:
                return "yes"
        return "no"


def test_link_entities_to_code_creates_mentions_for_confirmed_matches(graph_store):
    graph_store.upsert_text_entities([
        {"id": "e1", "user_id": "u1", "name": "Retriever", "entity_type": "concept",
         "source_doc_id": "doc-1", "source_memory_id": None},
    ])
    graph_store.upsert_symbols([
        {"id": "s1", "user_id": "u1", "repo_id": "r1", "kind": "class", "name": "Retriever",
         "file_path": "retrieval.py", "start_line": 1, "end_line": 10, "language": "python"},
        {"id": "s2", "user_id": "u1", "repo_id": "r1", "kind": "function", "name": "unrelated_helper",
         "file_path": "utils.py", "start_line": 1, "end_line": 2, "language": "python"},
    ])
    llm = _FakeLLM(confirm_names={"Retriever"})
    embedder = _FakeEmbedder()

    linked = link_entities_to_code(graph_store, llm, embedder, "u1", "doc-1")

    assert linked == 1
    assert graph_store.mentions_edges == [{"source": "e1", "target": "s1"}]


def test_link_entities_to_code_returns_zero_when_no_symbols(graph_store):
    graph_store.upsert_text_entities([
        {"id": "e1", "user_id": "u1", "name": "Retriever", "entity_type": "concept",
         "source_doc_id": "doc-1", "source_memory_id": None},
    ])

    linked = link_entities_to_code(graph_store, _FakeLLM(set()), _FakeEmbedder(), "u1", "doc-1")

    assert linked == 0
    assert graph_store.mentions_edges == []


def test_link_entities_to_code_ignores_entities_from_other_documents(graph_store):
    graph_store.upsert_text_entities([
        {"id": "e1", "user_id": "u1", "name": "Retriever", "entity_type": "concept",
         "source_doc_id": "doc-other", "source_memory_id": None},
    ])
    graph_store.upsert_symbols([
        {"id": "s1", "user_id": "u1", "repo_id": "r1", "kind": "class", "name": "Retriever",
         "file_path": "retrieval.py", "start_line": 1, "end_line": 10, "language": "python"},
    ])

    linked = link_entities_to_code(graph_store, _FakeLLM({"Retriever"}), _FakeEmbedder(), "u1", "doc-1")

    assert linked == 0
