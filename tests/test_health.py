from app.dashboard import health


class _OkQdrant:
    def get_collections(self):
        return object()


class _DownQdrant:
    def get_collections(self):
        raise ConnectionError("refused")


class _Embedder:
    def embed_single(self, text):
        return [0.1]


def test_check_qdrant_up():
    result = health.check_qdrant(_OkQdrant())
    assert result["name"] == "qdrant"
    assert result["up"] is True
    assert "latency_ms" in result


def test_check_qdrant_down():
    result = health.check_qdrant(_DownQdrant())
    assert result == {"name": "qdrant", "up": False, "error": "refused"}


def test_check_neo4j_up(graph_store):
    result = health.check_neo4j(graph_store)
    assert result["name"] == "neo4j"
    assert result["up"] is True


def test_check_postgres_up(usage_store):
    result = health.check_postgres(usage_store)
    assert result["name"] == "postgres"
    assert result["up"] is True


def test_check_postgres_disabled_when_store_is_none():
    result = health.check_postgres(None)
    assert result == {"name": "postgres", "up": False, "error": "usage tracking disabled (USAGE_DB_URL unset)"}


def test_check_embedder_loaded():
    result = health.check_embedder(_Embedder())
    assert result == {"name": "embed_model", "up": True}


def test_check_embedder_not_loaded():
    result = health.check_embedder(None)
    assert result == {"name": "embed_model", "up": False, "error": "embedder not loaded"}
