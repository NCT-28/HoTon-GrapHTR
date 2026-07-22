import os

from app.config import Settings


def test_settings_defaults(monkeypatch):
    monkeypatch.delenv("QDRANT_URL", raising=False)
    monkeypatch.delenv("EMBED_MODEL_NAME", raising=False)
    settings = Settings()
    assert settings.qdrant_url == "http://localhost:6333"
    assert settings.embed_model_name == "sentence-transformers/all-MiniLM-L6-v2"
    assert settings.embed_dim == 384
    assert settings.chunk_size == 1800
    assert settings.chunk_overlap == 180


def test_settings_reads_env(monkeypatch):
    monkeypatch.setenv("QDRANT_URL", "http://qdrant:6333")
    monkeypatch.setenv("EMBED_MODEL_NAME", "custom/model")
    settings = Settings()
    assert settings.qdrant_url == "http://qdrant:6333"
    assert settings.embed_model_name == "custom/model"


def test_settings_dashboard_and_usage_db_defaults(monkeypatch):
    monkeypatch.delenv("USAGE_DB_URL", raising=False)
    monkeypatch.delenv("DASHBOARD_USER", raising=False)
    monkeypatch.delenv("DASHBOARD_PASSWORD", raising=False)
    settings = Settings()
    assert settings.usage_db_url == ""
    assert settings.dashboard_user == ""
    assert settings.dashboard_password == ""


def test_settings_reads_dashboard_and_usage_db_env(monkeypatch):
    monkeypatch.setenv("USAGE_DB_URL", "postgresql://lmr:changeme@postgres:5432/hoton_rag")
    monkeypatch.setenv("DASHBOARD_USER", "admin")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "secret")
    settings = Settings()
    assert settings.usage_db_url == "postgresql://lmr:changeme@postgres:5432/hoton_rag"
    assert settings.dashboard_user == "admin"
    assert settings.dashboard_password == "secret"
