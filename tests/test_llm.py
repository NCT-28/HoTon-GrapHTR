import time
from unittest.mock import patch

from app.clients.llm import ReasoningLLM, get_reasoning_llm, unload_reasoning_llm


class FakeGenerator:
    def __init__(self, response_text: str):
        self._response_text = response_text
        self.last_call_kwargs = None

    def __call__(self, messages, **kwargs):
        self.last_call_kwargs = kwargs
        return [{"generated_text": self._response_text}]


def test_generate_returns_text():
    llm = ReasoningLLM(model_name="fake", idle_unload_seconds=3600, generator=FakeGenerator("hello back"))
    result = llm.generate("hi", max_new_tokens=64, temperature=0.2)
    assert result == "hello back"


def test_generate_passes_sampling_params():
    fake = FakeGenerator("ok")
    llm = ReasoningLLM(model_name="fake", idle_unload_seconds=3600, generator=fake)
    llm.generate("hi", max_new_tokens=100, temperature=0.5)
    assert fake.last_call_kwargs["max_new_tokens"] == 100
    assert fake.last_call_kwargs["temperature"] == 0.5
    assert fake.last_call_kwargs["do_sample"] is True


def test_generate_zero_temperature_disables_sampling():
    fake = FakeGenerator("ok")
    llm = ReasoningLLM(model_name="fake", idle_unload_seconds=3600, generator=fake)
    llm.generate("hi", max_new_tokens=10, temperature=0.0)
    assert fake.last_call_kwargs["do_sample"] is False


def test_generate_loads_pipeline_on_gpu_device_when_cuda_available():
    with patch("app.clients.llm.torch.cuda.is_available", return_value=True), \
         patch("app.clients.llm.pipeline") as mock_pipeline:
        mock_pipeline.return_value = FakeGenerator("ok")
        llm = ReasoningLLM(model_name="fake", idle_unload_seconds=3600)
        llm.generate("hi")
        assert mock_pipeline.call_args.kwargs["device"] == 0


def test_generate_loads_pipeline_on_cpu_device_when_cuda_unavailable():
    with patch("app.clients.llm.torch.cuda.is_available", return_value=False), \
         patch("app.clients.llm.pipeline") as mock_pipeline:
        mock_pipeline.return_value = FakeGenerator("ok")
        llm = ReasoningLLM(model_name="fake", idle_unload_seconds=3600)
        llm.generate("hi")
        assert mock_pipeline.call_args.kwargs["device"] == -1


def test_generate_reuses_loaded_pipeline_across_calls():
    with patch("app.clients.llm.pipeline") as mock_pipeline:
        mock_pipeline.return_value = FakeGenerator("ok")
        llm = ReasoningLLM(model_name="fake", idle_unload_seconds=3600)
        llm.generate("hi")
        llm.generate("hi again")
        assert mock_pipeline.call_count == 1


def test_unload_clears_generator_and_reloads_on_next_generate():
    with patch("app.clients.llm.pipeline") as mock_pipeline:
        mock_pipeline.return_value = FakeGenerator("ok")
        llm = ReasoningLLM(model_name="fake", idle_unload_seconds=3600)
        llm.generate("hi")
        llm.unload()
        llm.generate("hi")
        assert mock_pipeline.call_count == 2


def test_generate_auto_unloads_after_idle_timeout():
    with patch("app.clients.llm.pipeline") as mock_pipeline:
        mock_pipeline.return_value = FakeGenerator("ok")
        llm = ReasoningLLM(model_name="fake", idle_unload_seconds=0.05)
        llm.generate("hi")
        assert llm._generator is not None
        time.sleep(0.2)
        assert llm._generator is None


def test_unload_reasoning_llm_unloads_the_cached_singleton():
    get_reasoning_llm.cache_clear()
    with patch("app.clients.llm.pipeline") as mock_pipeline:
        mock_pipeline.return_value = FakeGenerator("ok")
        llm = get_reasoning_llm()
        llm.generate("hi")
        assert llm._generator is not None
        unload_reasoning_llm()
        assert llm._generator is None
        # unloading keeps the same cached instance (no duplicate reload elsewhere)
        assert get_reasoning_llm() is llm
