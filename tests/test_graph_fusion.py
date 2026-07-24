from app.agentic.graph_fusion import extract_graph_keywords


class FakeLLM:
    def __init__(self, response_text):
        self._response_text = response_text
        self.last_prompt = None

    def generate(self, prompt, max_new_tokens=60, temperature=0.0):
        self.last_prompt = prompt
        return self._response_text


def test_extract_graph_keywords_parses_json_array():
    llm = FakeLLM('["retrieve_chunks", "Embedder"]')
    result = extract_graph_keywords(llm, "how does chunk retrieval work?")
    assert result == ["retrieve_chunks", "Embedder"]


def test_extract_graph_keywords_empty_array_returns_empty_list():
    llm = FakeLLM("[]")
    assert extract_graph_keywords(llm, "hi there") == []


def test_extract_graph_keywords_unparsable_text_returns_empty_list():
    llm = FakeLLM("I'm not sure what symbols are relevant here.")
    assert extract_graph_keywords(llm, "hi there") == []


def test_extract_graph_keywords_caps_at_three():
    llm = FakeLLM('["a", "b", "c", "d", "e"]')
    assert extract_graph_keywords(llm, "query") == ["a", "b", "c"]


def test_extract_graph_keywords_includes_query_in_prompt():
    llm = FakeLLM("[]")
    extract_graph_keywords(llm, "What is memory decay?")
    assert "What is memory decay?" in llm.last_prompt
