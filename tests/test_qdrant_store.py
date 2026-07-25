def test_bootstrap_creates_three_collections(qdrant):
    names = {c.name for c in qdrant.get_collections().collections}
    assert {"rag_documents", "rag_chunks", "user_memories"}.issubset(names)


def test_bootstrap_is_idempotent(qdrant):
    from app.clients.qdrant_store import bootstrap_collections

    # Calling again must not raise (e.g. "collection already exists")
    bootstrap_collections(qdrant, embed_dim=384)
    names = {c.name for c in qdrant.get_collections().collections}
    assert {"rag_documents", "rag_chunks", "user_memories"}.issubset(names)


def test_bootstrap_creates_profile_collections(qdrant):
    names = {c.name for c in qdrant.get_collections().collections}
    assert "user_profiles" in names
    assert "profile_snapshots" in names


def test_bootstrap_collections_creates_code_symbol_embeddings(qdrant):
    from app.clients.qdrant_store import CODE_SYMBOL_EMBEDDINGS

    existing = {c.name for c in qdrant.get_collections().collections}
    assert CODE_SYMBOL_EMBEDDINGS in existing


def test_bootstrap_collections_works_with_local_path_client(tmp_path):
    from qdrant_client import QdrantClient
    from app.clients.qdrant_store import bootstrap_collections

    client = QdrantClient(path=str(tmp_path / "qdrant"))
    bootstrap_collections(client, embed_dim=384)

    names = {c.name for c in client.get_collections().collections}
    assert {"rag_documents", "rag_chunks", "user_memories"}.issubset(names)


def test_get_qdrant_client_uses_local_path_in_local_deploy_mode(tmp_path, monkeypatch):
    from app.config import get_settings
    from app.clients.qdrant_store import get_qdrant_client

    monkeypatch.setenv("DEPLOY_MODE", "local")
    monkeypatch.setenv("LOCAL_DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    get_qdrant_client.cache_clear()

    client = get_qdrant_client()

    names = {c.name for c in client.get_collections().collections}
    assert "rag_chunks" in names
    assert (tmp_path / "qdrant").is_dir()

    get_qdrant_client.cache_clear()
    get_settings.cache_clear()


def test_get_repo_qdrant_client_creates_embedded_client_under_repo_graphtr_out(tmp_path):
    from app.clients.qdrant_store import CODE_SYMBOL_EMBEDDINGS, get_repo_qdrant_client

    repo_path = str(tmp_path / "repo1")
    client = get_repo_qdrant_client(repo_path)

    names = {c.name for c in client.get_collections().collections}
    assert CODE_SYMBOL_EMBEDDINGS in names
    assert (tmp_path / "repo1" / "graphtr-out" / "qdrant").is_dir()

    get_repo_qdrant_client.cache_clear()


def test_get_repo_qdrant_client_caches_by_local_path(tmp_path):
    from app.clients.qdrant_store import get_repo_qdrant_client

    repo_path = str(tmp_path / "repo1")

    assert get_repo_qdrant_client(repo_path) is get_repo_qdrant_client(repo_path)

    get_repo_qdrant_client.cache_clear()


def test_get_repo_qdrant_client_returns_distinct_clients_for_different_repos(tmp_path):
    from app.clients.qdrant_store import get_repo_qdrant_client

    client1 = get_repo_qdrant_client(str(tmp_path / "repo1"))
    client2 = get_repo_qdrant_client(str(tmp_path / "repo2"))

    assert client1 is not client2

    get_repo_qdrant_client.cache_clear()
