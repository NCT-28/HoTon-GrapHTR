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


def test_link_entities_to_code_is_a_no_op_without_stored_code_symbols(graph_store):
    # ingest_codebase writes graphtr-out/ instead of storing code symbols, so
    # list_code_symbols is always empty and entity->code linking never fires.
    graph_store.upsert_text_entities([
        {"id": "e1", "user_id": "u1", "name": "Retriever", "entity_type": "concept",
         "source_doc_id": "doc-1", "source_memory_id": None},
    ])

    linked = link_entities_to_code(graph_store, _FakeLLM({"Retriever"}), _FakeEmbedder(), "u1", "doc-1")

    assert linked == 0
    assert graph_store.mentions_edges == []


def test_link_entities_to_code_returns_zero_when_no_entities_for_the_document(graph_store):
    graph_store.upsert_text_entities([
        {"id": "e1", "user_id": "u1", "name": "Retriever", "entity_type": "concept",
         "source_doc_id": "doc-other", "source_memory_id": None},
    ])

    linked = link_entities_to_code(graph_store, _FakeLLM({"Retriever"}), _FakeEmbedder(), "u1", "doc-1")

    assert linked == 0
