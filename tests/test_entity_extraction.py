from app.graph.entity_extraction import extract_and_store_entities, parse_entities_from_text


class _FakeLLM:
    def __init__(self, response: str):
        self._response = response

    def generate(self, prompt: str, max_new_tokens: int = 256, temperature: float = 0.1) -> str:
        return self._response


def test_parse_entities_from_text_extracts_entities_and_relationships():
    raw = '''{"entities": [{"name": "Retriever", "type": "concept"}, {"name": "Ranker", "type": "concept"}],
    "relationships": [{"source": "Retriever", "target": "Ranker"}]}'''

    entities, relationships = parse_entities_from_text(raw)

    assert {e.name for e in entities} == {"Retriever", "Ranker"}
    assert len(relationships) == 1
    assert relationships[0].source_name == "Retriever"
    assert relationships[0].target_name == "Ranker"


def test_parse_entities_from_text_drops_relationships_with_unknown_entities():
    raw = '{"entities": [{"name": "A", "type": "concept"}], "relationships": [{"source": "A", "target": "B"}]}'

    entities, relationships = parse_entities_from_text(raw)

    assert len(entities) == 1
    assert relationships == []


def test_parse_entities_from_text_handles_invalid_json():
    entities, relationships = parse_entities_from_text("not json at all")
    assert entities == []
    assert relationships == []


def test_extract_and_store_entities_writes_to_graph_store(graph_store):
    llm = _FakeLLM('{"entities": [{"name": "Retriever", "type": "concept"}], "relationships": []}')

    stored = extract_and_store_entities(graph_store, llm, "user-1", "doc-1", "Some document text about retrievers.")

    assert stored == 1
    entities = graph_store.list_text_entities("user-1")
    assert entities[0]["name"] == "Retriever"
    assert entities[0]["source_doc_id"] == "doc-1"


def test_extract_and_store_entities_returns_zero_when_none_found(graph_store):
    llm = _FakeLLM('{"entities": [], "relationships": []}')

    stored = extract_and_store_entities(graph_store, llm, "user-1", "doc-1", "Nothing notable here.")

    assert stored == 0
    assert graph_store.list_text_entities("user-1") == []
