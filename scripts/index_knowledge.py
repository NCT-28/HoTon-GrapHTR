#!/usr/bin/env python3
"""Uploads graphtr-out/knowledge/*.md into hoton-graphtr's document RAG under a
dedicated project rag_user_id (minted into manifest.json on first run).
Full-regenerate: deletes any existing doc with the same title before
re-uploading. Run via: python3 hoton-graphtr/scripts/index_knowledge.py

Note: hoton-graphtr's /api/documents upload derives `title` from the uploaded
filename's stem (see app/rag/documents.py::process_uploaded_file), not from
any field we send -- so a "<topic>.md" upload is always stored with title
"<topic>" (lowercase). delete_existing() below matches on that same lowercase
topic string, not a display name, or it would never find the doc to delete.
"""
import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

def _find_repo_root(start: Path) -> Path:
    """Walk up from `start` looking for a `.git` directory. Marker-based (not a
    fixed parent-count) because this script is bundled at different depths in
    different projects: hoton-graphtr/scripts/ in this repo, but
    .claude/skills/graphtr-knowledge/scripts/ in a project this was installed
    into via init_graphtr_skills.py."""
    start = start.resolve()
    for candidate in [start, *start.parents]:
        if (candidate / ".git").exists():
            return candidate
    raise RuntimeError(f"could not find repo root (no .git found) walking up from {start}")


REPO_ROOT = _find_repo_root(Path(__file__).parent)
MANIFEST_PATH = REPO_ROOT / "graphtr-out" / "manifest.json"
KNOWLEDGE_DIR = REPO_ROOT / "graphtr-out" / "knowledge"

TOPICS = ["architecture", "concerns", "conventions", "integrations", "stack", "structure", "testing"]


def build_multipart(field_name: str, filename: str, content: bytes):
    boundary = uuid.uuid4().hex
    parts = [
        f"--{boundary}".encode(),
        f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"'.encode(),
        b"Content-Type: text/markdown",
        b"",
        content,
        f"--{boundary}--".encode(),
        b"",
    ]
    body = b"\r\n".join(parts)
    content_type = f"multipart/form-data; boundary={boundary}"
    return body, content_type


def http_request(method: str, url: str, headers=None, body=None):
    req = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            data = resp.read()
            return resp.status, (json.loads(data) if data else {})
    except urllib.error.HTTPError as e:
        data = e.read()
        return e.code, (json.loads(data) if data else {})


def get_or_mint_rag_user_id(manifest: dict) -> str:
    if manifest.get("rag_user_id"):
        return manifest["rag_user_id"]
    rag_user_id = str(uuid.uuid4())
    manifest["rag_user_id"] = rag_user_id
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + "\n")
    return rag_user_id


def delete_existing(base_url: str, rag_user_id: str, topic: str) -> None:
    status, data = http_request("GET", f"{base_url}/api/documents", headers={"X-User-Id": rag_user_id})
    if status != 200:
        return
    for doc in data.get("documents", []):
        if doc.get("title") == topic:
            http_request(
                "DELETE", f"{base_url}/api/documents/{doc['id']}", headers={"X-User-Id": rag_user_id}
            )


def upload(base_url: str, rag_user_id: str, topic: str):
    path = KNOWLEDGE_DIR / f"{topic}.md"
    content = path.read_bytes()
    body, content_type = build_multipart("file", f"{topic}.md", content)
    return http_request(
        "POST",
        f"{base_url}/api/documents",
        headers={"X-User-Id": rag_user_id, "Content-Type": content_type},
        body=body,
    )


def chunk_count_for(base_url: str, rag_user_id: str, document_id: str) -> int:
    status, data = http_request(
        "GET", f"{base_url}/api/documents/{document_id}", headers={"X-User-Id": rag_user_id}
    )
    return data.get("chunk_count", 0) if status == 200 else 0


def main() -> None:
    base_url = os.environ.get("RAG_SERVICE_URL", "http://localhost:8030")
    manifest = json.loads(MANIFEST_PATH.read_text())
    rag_user_id = get_or_mint_rag_user_id(manifest)

    results = []
    for topic in TOPICS:
        path = KNOWLEDGE_DIR / f"{topic}.md"
        if not path.exists():
            print(f"FAIL: {path} does not exist -- run build_knowledge_skeleton.py + fill narrative first")
            sys.exit(1)
        delete_existing(base_url, rag_user_id, topic)
        status, data = upload(base_url, rag_user_id, topic)
        if status != 202:
            print(f"FAIL {topic}: HTTP {status} {data}")
            sys.exit(1)
        doc_id = data["document_id"]
        results.append((topic, doc_id, chunk_count_for(base_url, rag_user_id, doc_id)))

    print(f"rag_user_id: {rag_user_id}")
    for topic, doc_id, chunk_count in results:
        print(f"  {topic}: document_id={doc_id} chunks={chunk_count}")


if __name__ == "__main__":
    main()
