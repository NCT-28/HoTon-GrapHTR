from functools import lru_cache

from sentence_transformers import SentenceTransformer

from app.config import get_settings


class Embedder:
    def __init__(self, model, dim: int):
        self._model = model
        self._dim = dim

    @property
    def dim(self) -> int:
        return self._dim

    def embed_single(self, text: str) -> list[float]:
        return self.embed_batch([text])[0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        vectors = self._model.encode(texts, normalize_embeddings=True)
        return [v.tolist() for v in vectors]


@lru_cache
def get_embedder() -> Embedder:
    settings = get_settings()
    model = SentenceTransformer(settings.embed_model_name)
    return Embedder(model=model, dim=settings.embed_dim)
