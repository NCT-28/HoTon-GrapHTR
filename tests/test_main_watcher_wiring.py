from unittest.mock import MagicMock


class _FakeEmbedder:
    def embed_batch(self, texts):
        return [[0.0] * 384 for _ in texts]


class _FakeLLM:
    def generate(self, *args, **kwargs):
        return ""


def _clear_caches():
    from app.config import get_settings
    from app.graph.code_graph_store import get_graph_store
    from app.clients.qdrant_store import get_qdrant_client, get_repo_qdrant_client

    get_settings.cache_clear()
    get_graph_store.cache_clear()
    get_qdrant_client.cache_clear()
    get_repo_qdrant_client.cache_clear()


def _capture_watcher_manager_kwargs(monkeypatch, main_module):
    captured = {}
    real_watcher_manager = main_module.RepoWatcherManager

    def spy(*args, **kwargs):
        captured["kwargs"] = kwargs
        return real_watcher_manager(*args, **kwargs)

    monkeypatch.setattr(main_module, "RepoWatcherManager", spy)
    return captured


def test_create_app_wires_repo_qdrant_resolver_in_local_deploy_mode(tmp_path, monkeypatch):
    import app.main as main_module
    from app.clients.qdrant_store import get_repo_qdrant_client

    monkeypatch.setenv("DEPLOY_MODE", "local")
    monkeypatch.setenv("LOCAL_DATA_DIR", str(tmp_path))
    _clear_caches()
    captured = _capture_watcher_manager_kwargs(monkeypatch, main_module)

    main_module.create_app(embedder=_FakeEmbedder(), llm=_FakeLLM(), web_search_fn=lambda q: [])

    assert captured["kwargs"]["qdrant_client_resolver"] is get_repo_qdrant_client
    _clear_caches()


def test_create_app_does_not_wire_resolver_when_qdrant_client_explicitly_injected(tmp_path, monkeypatch):
    import app.main as main_module

    monkeypatch.setenv("DEPLOY_MODE", "local")
    monkeypatch.setenv("LOCAL_DATA_DIR", str(tmp_path))
    _clear_caches()
    captured = _capture_watcher_manager_kwargs(monkeypatch, main_module)

    main_module.create_app(
        qdrant_client=MagicMock(), embedder=_FakeEmbedder(), llm=_FakeLLM(), web_search_fn=lambda q: [],
    )

    assert captured["kwargs"]["qdrant_client_resolver"] is None
    _clear_caches()
