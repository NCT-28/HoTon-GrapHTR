import pytest

from app.graph.repo_source import resolve_repo_source


def test_resolve_repo_source_returns_existing_local_path(tmp_path):
    assert resolve_repo_source(str(tmp_path)) == str(tmp_path)


def test_resolve_repo_source_rejects_missing_local_path(tmp_path):
    with pytest.raises(ValueError, match="does not exist"):
        resolve_repo_source(str(tmp_path / "missing"))
