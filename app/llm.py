from functools import lru_cache

import torch
from transformers import pipeline

from app.config import get_settings


class ReasoningLLM:
    def __init__(self, generator):
        self._generator = generator

    def generate(self, user_message: str, max_new_tokens: int = 256, temperature: float = 0.1) -> str:
        messages = [{"role": "user", "content": user_message}]
        outputs = self._generator(
            messages,
            max_new_tokens=max_new_tokens,
            temperature=temperature,
            do_sample=temperature > 0,
            return_full_text=False,
        )
        return outputs[0]["generated_text"]


@lru_cache
def get_reasoning_llm() -> ReasoningLLM:
    settings = get_settings()
    device = 0 if torch.cuda.is_available() else -1
    generator = pipeline("text-generation", model=settings.reasoning_model_name, device=device)
    return ReasoningLLM(generator=generator)
