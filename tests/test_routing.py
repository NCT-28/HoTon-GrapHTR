from app.routing import QueryComplexity, classify_query


class FakeLLM:
    def __init__(self, response_text):
        self._response_text = response_text

    def generate(self, prompt, max_new_tokens=10, temperature=0.0):
        return self._response_text


def test_classify_direct():
    assert classify_query(FakeLLM("direct"), "hi there") == QueryComplexity.DIRECT


def test_classify_single():
    assert classify_query(FakeLLM("single"), "what does our onboarding doc say about X?") == QueryComplexity.SINGLE


def test_classify_multi():
    assert classify_query(FakeLLM("multi"), "compare our Q1 and Q2 reports and summarize trends") == QueryComplexity.MULTI


def test_classify_unparsable_defaults_to_single():
    assert classify_query(FakeLLM("uh, not sure!"), "some query") == QueryComplexity.SINGLE


def test_classify_is_case_insensitive_and_trims_whitespace():
    assert classify_query(FakeLLM("  Direct  \n"), "hi") == QueryComplexity.DIRECT
