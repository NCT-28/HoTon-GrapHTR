import pytest

from app.grading import CRAG_RELEVANCE_THRESHOLD, crag_correct, grade_relevance
from app.retrieval import RetrievedChunk


class FakeLLM:
    def __init__(self, response_text):
        self._response_text = response_text

    def generate(self, prompt, max_new_tokens=10, temperature=0.0):
        return self._response_text


def test_grade_relevance_parses_number():
    assert grade_relevance(FakeLLM("0.8"), "q", "passage") == 0.8


def test_grade_relevance_clamps_out_of_range():
    assert grade_relevance(FakeLLM("1.5"), "q", "passage") == 1.0
    assert grade_relevance(FakeLLM("-0.3"), "q", "passage") == 0.0


def test_grade_relevance_unparsable_defaults_neutral():
    assert grade_relevance(FakeLLM("not a number"), "q", "passage") == 0.5


@pytest.mark.asyncio
async def test_crag_correct_keeps_chunks_when_relevance_high():
    chunks = [RetrievedChunk(id="1", content="on topic", document_title="Doc", source_url=None, similarity=0.9, document_expired=False)]

    async def web_search_fn(query):
        raise AssertionError("web search should not be called when relevance is high")

    result = await crag_correct(FakeLLM("0.9"), web_search_fn, "query", chunks)
    assert result == chunks


@pytest.mark.asyncio
async def test_crag_correct_falls_back_to_web_search_when_relevance_low():
    chunks = [RetrievedChunk(id="1", content="off topic", document_title="Doc", source_url=None, similarity=0.3, document_expired=False)]

    async def web_search_fn(query):
        return ["fresh web snippet"]

    result = await crag_correct(FakeLLM("0.2"), web_search_fn, "query", chunks)
    assert len(result) == 2
    assert result[1].content == "fresh web snippet"
    assert result[1].document_title == "Web Search"


@pytest.mark.asyncio
async def test_crag_correct_with_no_chunks_always_falls_back():
    async def web_search_fn(query):
        return ["only web result"]

    result = await crag_correct(FakeLLM("0.9"), web_search_fn, "query", [])
    assert len(result) == 1
    assert result[0].content == "only web result"


def test_relevance_threshold_is_reasonable():
    assert 0.0 < CRAG_RELEVANCE_THRESHOLD < 1.0
