def test_bootstrap_creates_three_collections(qdrant):
    names = {c.name for c in qdrant.get_collections().collections}
    assert {"rag_documents", "rag_chunks", "user_memories"}.issubset(names)


def test_bootstrap_is_idempotent(qdrant):
    from app.qdrant_store import bootstrap_collections

    # Calling again must not raise (e.g. "collection already exists")
    bootstrap_collections(qdrant, embed_dim=384)
    names = {c.name for c in qdrant.get_collections().collections}
    assert {"rag_documents", "rag_chunks", "user_memories"}.issubset(names)


def test_bootstrap_creates_profile_collections(qdrant):
    names = {c.name for c in qdrant.get_collections().collections}
    assert "user_profiles" in names
    assert "profile_snapshots" in names


def test_bootstrap_collections_creates_code_symbol_embeddings(qdrant):
    from app.qdrant_store import CODE_SYMBOL_EMBEDDINGS

    existing = {c.name for c in qdrant.get_collections().collections}
    assert CODE_SYMBOL_EMBEDDINGS in existing
