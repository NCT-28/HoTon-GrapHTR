from app.agentic.hyde import generate_hypothetical_answer


class FakeLLM:
    def __init__(self, response_text):
        self._response_text = response_text
        self.last_prompt = None

    def generate(self, prompt, max_new_tokens=150, temperature=0.3):
        self.last_prompt = prompt
        return self._response_text


def test_generate_hypothetical_answer_returns_trimmed_text():
    llm = FakeLLM("  This is what the answer would look like.  \n")
    result = generate_hypothetical_answer(llm, "What is our refund policy?")
    assert result == "This is what the answer would look like."


def test_generate_hypothetical_answer_includes_query_in_prompt():
    llm = FakeLLM("answer")
    generate_hypothetical_answer(llm, "What is our refund policy?")
    assert "What is our refund policy?" in llm.last_prompt
