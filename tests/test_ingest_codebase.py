import json

import pytest

from app.graph.ingest import IngestCodebaseResult, ingest_codebase_impl


def _fixture_repo(tmp_path):
    (tmp_path / "mod.py").write_text(
        "def helper():\n"
        "    return 1\n"
        "\n"
        "def caller():\n"
        "    return helper()\n"
    )
    return tmp_path


def test_ingest_codebase_writes_graph_manifest_and_viewer(tmp_path):
    repo = _fixture_repo(tmp_path)

    result = ingest_codebase_impl(str(repo))

    out_dir = repo / "graphtr-out"
    assert isinstance(result, IngestCodebaseResult)
    assert (out_dir / "graph.json").exists()
    assert (out_dir / "manifest.json").exists()
    assert (out_dir / "graphtr.html").exists()


def test_ingest_codebase_result_counts_match_graph_json(tmp_path):
    repo = _fixture_repo(tmp_path)

    result = ingest_codebase_impl(str(repo))

    graph = json.loads((repo / "graphtr-out" / "graph.json").read_text())
    assert result.symbol_count == len(graph["nodes"])
    assert result.edge_count == len(graph["edges"])
    assert result.symbol_count > 0


def test_ingest_codebase_mints_a_fresh_repo_id_every_call(tmp_path):
    repo = _fixture_repo(tmp_path)

    first = ingest_codebase_impl(str(repo))
    second = ingest_codebase_impl(str(repo))

    assert first.repo_id != second.repo_id


def test_ingest_codebase_rejects_git_urls(tmp_path):
    with pytest.raises(ValueError, match="git URLs are not supported"):
        ingest_codebase_impl("https://github.com/example/repo.git")


def test_ingest_codebase_preserves_rag_user_id_in_manifest(tmp_path):
    repo = _fixture_repo(tmp_path)
    out_dir = repo / "graphtr-out"
    out_dir.mkdir()
    (out_dir / "manifest.json").write_text(json.dumps({"rag_user_id": "keep-me"}))

    ingest_codebase_impl(str(repo))

    manifest = json.loads((out_dir / "manifest.json").read_text())
    assert manifest["rag_user_id"] == "keep-me"
