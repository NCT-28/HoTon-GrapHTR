from app.llm import ReasoningLLM


class FakeGenerator:
    def __init__(self, response_text: str):
        self._response_text = response_text
        self.last_call_kwargs = None

    def __call__(self, messages, **kwargs):
        self.last_call_kwargs = kwargs
        return [{"generated_text": self._response_text}]


def test_generate_returns_text():
    llm = ReasoningLLM(generator=FakeGenerator("hello back"))
    result = llm.generate("hi", max_new_tokens=64, temperature=0.2)
    assert result == "hello back"


def test_generate_passes_sampling_params():
    fake = FakeGenerator("ok")
    llm = ReasoningLLM(generator=fake)
    llm.generate("hi", max_new_tokens=100, temperature=0.5)
    assert fake.last_call_kwargs["max_new_tokens"] == 100
    assert fake.last_call_kwargs["temperature"] == 0.5
    assert fake.last_call_kwargs["do_sample"] is True


def test_generate_zero_temperature_disables_sampling():
    fake = FakeGenerator("ok")
    llm = ReasoningLLM(generator=fake)
    llm.generate("hi", max_new_tokens=10, temperature=0.0)
    assert fake.last_call_kwargs["do_sample"] is False
