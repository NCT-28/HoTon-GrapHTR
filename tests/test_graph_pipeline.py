import pytest

from app.graph.graph_pipeline import run_entity_extraction_and_linking


class _FakeLLM:
    def __init__(self, extraction_response: str, confirm: bool):
        self._extraction_response = extraction_response
        self._confirm = confirm

    def generate(self, prompt: str, max_new_tokens: int = 256, temperature: float = 0.1) -> str:
        if "Extract 0-5 named entities" in prompt:
            return self._extraction_response
        return "yes" if self._confirm else "no"


class _FakeEmbedder:
    def embed_single(self, text: str) -> list[float]:
        return [float(len(text))]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_single(t) for t in texts]


@pytest.mark.asyncio
async def test_run_entity_extraction_and_linking_creates_entities_and_mentions(graph_store):
    graph_store.upsert_symbols([
        {"id": "s1", "user_id": "u1", "repo_id": "r1", "kind": "class", "name": "Retriever",
         "file_path": "retrieval.py", "start_line": 1, "end_line": 10, "language": "python"},
    ])
    llm = _FakeLLM(
        extraction_response='{"entities": [{"name": "Retriever", "type": "concept"}], "relationships": []}',
        confirm=True,
    )

    await run_entity_extraction_and_linking(graph_store, llm, _FakeEmbedder(), "u1", "doc-1", "Text about a Retriever.")

    assert graph_store.list_text_entities("u1")
    assert graph_store.mentions_edges


@pytest.mark.asyncio
async def test_run_entity_extraction_and_linking_swallows_errors(graph_store):
    class _BrokenLLM:
        def generate(self, *a, **kw):
            raise RuntimeError("boom")

    # Must not raise — this runs as a detached background task where nothing
    # would observe an exception anyway.
    await run_entity_extraction_and_linking(graph_store, _BrokenLLM(), None, "u1", "doc-1", "text")
