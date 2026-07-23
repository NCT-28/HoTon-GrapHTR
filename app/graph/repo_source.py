"""Resolves an `ingest_codebase` source (local path or git URL) into a local
directory ready for parsing/watching. Git URLs are cloned under
CODE_REPOS_DIR; the same private-network/cloud-metadata blocklist already
used for document URL ingest (documents.py::is_safe_url) guards against
SSRF-style abuse here too. Only http(s) URLs are treated as git sources —
scp-style (git@host:path) URLs are rejected rather than accepted without a
safety check."""

import os
import shutil
import subprocess

from app.config import get_settings
from app.rag.documents import is_safe_url


def resolve_repo_source(source: str, repo_id: str) -> str:
    if source.startswith(("http://", "https://")):
        if not is_safe_url(source):
            raise ValueError("git URL targets a blocked or private address")

        settings = get_settings()
        os.makedirs(settings.code_repos_dir, exist_ok=True)
        dest = os.path.join(settings.code_repos_dir, repo_id)
        if os.path.exists(dest):
            # dest is a managed clone directory scoped to this repo_id, safe to
            # clear — `git clone` refuses to clone into an existing non-empty
            # directory, which would otherwise break any re-ingest/refresh of
            # the same repo_id with an uncaught CalledProcessError.
            shutil.rmtree(dest)
        subprocess.run(["git", "clone", "--depth", "1", source, dest], check=True, capture_output=True)
        return dest

    if not os.path.isdir(source):
        raise ValueError(f"local path does not exist or is not a directory: {source}")
    return source
