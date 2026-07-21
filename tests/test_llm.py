from unittest.mock import patch

from app.llm import ReasoningLLM, get_reasoning_llm


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


def test_get_reasoning_llm_uses_gpu_device_when_cuda_available():
    get_reasoning_llm.cache_clear()
    with patch("app.llm.torch.cuda.is_available", return_value=True), \
         patch("app.llm.pipeline") as mock_pipeline:
        get_reasoning_llm()
        assert mock_pipeline.call_args.kwargs["device"] == 0


def test_get_reasoning_llm_uses_cpu_device_when_cuda_unavailable():
    get_reasoning_llm.cache_clear()
    with patch("app.llm.torch.cuda.is_available", return_value=False), \
         patch("app.llm.pipeline") as mock_pipeline:
        get_reasoning_llm()
        assert mock_pipeline.call_args.kwargs["device"] == -1
