"""Document ingest — ported from hoton-lmr/src/rag/documents.rs (upload/extract/chunk/embed/store)."""

import ipaddress
import uuid
from urllib.parse import urlparse

from markdown_it import MarkdownIt
from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct

from app.chunker import DEFAULT_CHUNK_SIZE, DEFAULT_OVERLAP, chunk_text
from app.qdrant_store import RAG_CHUNKS

_BLOCKED_HOSTS = {
    "169.254.169.254",
    "fd00:ec2::254",
    "metadata.google.internal",
    "metadata.aws.internal",
    "localhost",
}


def extract_text_from_markdown(md: str) -> str:
    parser = MarkdownIt()
    tokens = parser.parse(md)
    parts: list[str] = []

    def walk(toks):
        for tok in toks:
            if tok.type == "text" or (tok.type == "inline" and tok.children is None):
                if tok.content:
                    parts.append(tok.content)
            if tok.children:
                walk(tok.children)

    walk(tokens)
    return " ".join(parts) + (" " if parts else "")


def process_uploaded_file(
    file_name: str | None, content_type: str | None, data: bytes
) -> tuple[str, str]:
    name = file_name or "unknown"
    title = name.split(".")[0]

    is_markdown = (content_type and content_type.startswith(("text/markdown", "text/plain"))) or name.endswith(
        ".md"
    )
    if is_markdown:
        text = extract_text_from_markdown(data.decode("utf-8", errors="replace"))
    elif name.endswith(".txt"):
        text = data.decode("utf-8", errors="replace")
    elif name.endswith(".pdf"):
        text = _extract_pdf_text(data)
    else:
        text = data.decode("utf-8", errors="replace")

    return title, text


def _extract_pdf_text(data: bytes) -> str:
    import io

    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def is_safe_url(raw: str) -> bool:
    """Returns False for URLs targeting loopback, private, or cloud-metadata addresses."""
    try:
        parsed = urlparse(raw)
    except ValueError:
        return False

    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False

    host = parsed.hostname.lower()
    if host in _BLOCKED_HOSTS:
        return False

    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return True  # not an IP literal — hostname, allowed

    if ip.is_loopback or ip.is_unspecified or ip.is_link_local or ip.is_private or ip.is_reserved:
        return False

    return True


def ingest_document(
    client: QdrantClient,
    embedder,
    document_id: uuid.UUID,
    user_id: uuid.UUID,
    text: str,
    document_title: str | None = None,
    source_url: str | None = None,
    valid_until: str | None = None,
) -> int:
    """Chunk, embed, and store a document's text. Returns the number of chunks stored."""
    chunks = chunk_text(text, DEFAULT_CHUNK_SIZE, DEFAULT_OVERLAP)
    if not chunks:
        return 0

    vectors = embedder.embed_batch(chunks)

    points = [
        PointStruct(
            id=str(uuid.uuid4()),
            vector=vector,
            payload={
                "document_id": str(document_id),
                "user_id": str(user_id),
                "chunk_index": idx,
                "content": chunk,
                "document_title": document_title,
                "source_url": source_url,
                "valid_until": valid_until,
            },
        )
        for idx, (chunk, vector) in enumerate(zip(chunks, vectors))
    ]

    client.upsert(collection_name=RAG_CHUNKS, points=points, wait=True)
    return len(points)
