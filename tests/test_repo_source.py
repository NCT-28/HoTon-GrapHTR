from unittest.mock import patch

import pytest

from app.config import get_settings
from app.repo_source import resolve_repo_source


def test_resolve_repo_source_returns_existing_local_path(tmp_path):
    assert resolve_repo_source(str(tmp_path), "repo-1") == str(tmp_path)


def test_resolve_repo_source_rejects_missing_local_path(tmp_path):
    with pytest.raises(ValueError, match="does not exist"):
        resolve_repo_source(str(tmp_path / "missing"), "repo-1")


def test_resolve_repo_source_rejects_private_git_url():
    with pytest.raises(ValueError, match="blocked or private"):
        resolve_repo_source("http://169.254.169.254/repo.git", "repo-1")


def test_resolve_repo_source_clones_public_git_url(tmp_path, monkeypatch):
    monkeypatch.setenv("CODE_REPOS_DIR", str(tmp_path))
    get_settings.cache_clear()

    with patch("app.repo_source.subprocess.run") as mock_run:
        dest = resolve_repo_source("https://github.com/example/repo.git", "repo-1")

    mock_run.assert_called_once_with(
        ["git", "clone", "--depth", "1", "https://github.com/example/repo.git", str(tmp_path / "repo-1")],
        check=True, capture_output=True,
    )
    assert dest == str(tmp_path / "repo-1")

    get_settings.cache_clear()
