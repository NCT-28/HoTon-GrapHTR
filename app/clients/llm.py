import threading
from functools import lru_cache

import torch
from transformers import pipeline

from app.config import get_settings


class ReasoningLLM:
    def __init__(self, model_name: str, idle_unload_seconds: float, generator=None):
        self._model_name = model_name
        self._idle_unload_seconds = idle_unload_seconds
        self._generator = generator
        self._lock = threading.Lock()
        self._idle_timer: threading.Timer | None = None

    def generate(self, user_message: str, max_new_tokens: int = 256, temperature: float = 0.1) -> str:
        messages = [{"role": "user", "content": user_message}]
        with self._lock:
            if self._generator is None:
                device = 0 if torch.cuda.is_available() else -1
                self._generator = pipeline("text-generation", model=self._model_name, device=device)
            outputs = self._generator(
                messages,
                max_new_tokens=max_new_tokens,
                temperature=temperature,
                do_sample=temperature > 0,
                return_full_text=False,
            )
            self._reset_idle_timer()
        return outputs[0]["generated_text"]

    def _reset_idle_timer(self) -> None:
        # caller already holds self._lock
        if self._idle_timer is not None:
            self._idle_timer.cancel()
        self._idle_timer = threading.Timer(self._idle_unload_seconds, self.unload)
        self._idle_timer.daemon = True
        self._idle_timer.start()

    def unload(self) -> None:
        with self._lock:
            if self._idle_timer is not None:
                self._idle_timer.cancel()
                self._idle_timer = None
            self._generator = None
        if torch.cuda.is_available():
            torch.cuda.empty_cache()


@lru_cache
def get_reasoning_llm() -> ReasoningLLM:
    settings = get_settings()
    return ReasoningLLM(
        model_name=settings.reasoning_model_name,
        idle_unload_seconds=settings.reasoning_model_idle_unload_seconds,
    )


def unload_reasoning_llm() -> None:
    get_reasoning_llm().unload()
