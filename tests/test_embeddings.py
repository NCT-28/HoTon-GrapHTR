from app.embeddings import Embedder


class FakeModel:
    def encode(self, texts, normalize_embeddings=True):
        # Deterministic fake vector: length of text mod 4 buckets, dim=384
        import numpy as np

        vecs = []
        for t in texts:
            v = np.zeros(384, dtype="float32")
            v[len(t) % 384] = 1.0
            vecs.append(v)
        return np.array(vecs)


def test_embed_single_returns_correct_dim():
    embedder = Embedder(model=FakeModel(), dim=384)
    vec = embedder.embed_single("hello world")
    assert len(vec) == 384
    assert isinstance(vec[0], float)


def test_embed_batch_returns_one_vector_per_text():
    embedder = Embedder(model=FakeModel(), dim=384)
    vecs = embedder.embed_batch(["a", "bb", "ccc"])
    assert len(vecs) == 3
    assert all(len(v) == 384 for v in vecs)
