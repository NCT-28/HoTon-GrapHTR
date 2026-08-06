"""Resolves an `ingest_codebase` source into a local directory ready for
parsing/watching. Only local paths are accepted — `ingest_codebase_impl`
rejects git URLs before this is ever called."""

import os


def resolve_repo_source(source: str) -> str:
    if not os.path.isdir(source):
        raise ValueError(f"local path does not exist or is not a directory: {source}")
    return source
