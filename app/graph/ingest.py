"""Code-graph ingestion: parse a local repo with tree-sitter and write the
result to <repo>/graphtr-out/. No LLM, no embedding model, no Qdrant/Neo4j —
this module's only dependencies are the graph submodules below, so it can be
imported on a machine that has none of the RAG stack installed."""
import uuid

from pydantic import BaseModel

from app.graph.code_parser import parse_repo
from app.graph.repo_source import resolve_repo_source
from app.graph.snapshot_writer import render_viewer, write_graph_snapshot


class IngestCodebaseResult(BaseModel):
    repo_id: str
    symbol_count: int
    edge_count: int


def ingest_codebase_impl(source: str) -> IngestCodebaseResult:
    # Git URLs would clone into the container and the graphtr-out/ written there
    # would be unreachable to the caller -- and with a fresh repo_id per call,
    # every clone would leak a new directory.
    if source.startswith(("http://", "https://")):
        raise ValueError("git URLs are not supported; clone the repo and pass a local path")

    repo_id = str(uuid.uuid4())
    local_path = resolve_repo_source(source)
    symbols, edges = parse_repo(repo_id, local_path)
    out_dir = write_graph_snapshot(local_path, repo_id, symbols, edges)
    render_viewer(out_dir)
    return IngestCodebaseResult(repo_id=repo_id, symbol_count=len(symbols), edge_count=len(edges))
