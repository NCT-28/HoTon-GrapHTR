import uuid

from app.rag.documents import (
    extract_text_from_markdown,
    ingest_document,
    is_safe_url,
    process_uploaded_file,
)
from app.clients.qdrant_store import RAG_CHUNKS


class FakeEmbedder:
    dim = 384

    def embed_batch(self, texts):
        return [[0.1] * 384 for _ in texts]


def test_extract_text_from_markdown_strips_formatting():
    md = "# Title\n\nSome **bold** and *italic* text."
    plain = extract_text_from_markdown(md)
    assert "Title" in plain
    assert "bold" in plain
    assert "**" not in plain


def test_process_uploaded_file_markdown():
    title, text = process_uploaded_file("notes.md", "text/markdown", b"# Hi\n\nBody text.")
    assert title == "notes"
    assert "Body text" in text


def test_process_uploaded_file_plain_text():
    # content_type=None (not "text/plain") is required to reach the plain-passthrough branch —
    # matches hoton-lmr/src/rag/documents.rs's exact routing: ct.starts_with("text/plain") always
    # routes through markdown extraction first, so a browser-set "text/plain" content-type never
    # reaches the .txt branch. Pre-existing quirk in the source being ported, not introduced here.
    title, text = process_uploaded_file("notes.txt", None, b"raw content here")
    assert title == "notes"
    assert text == "raw content here"


def test_is_safe_url_blocks_private_and_loopback():
    assert is_safe_url("http://example.com/page") is True
    assert is_safe_url("http://localhost/admin") is False
    assert is_safe_url("http://127.0.0.1/admin") is False
    assert is_safe_url("http://169.254.169.254/latest/meta-data") is False
    assert is_safe_url("http://192.168.1.1/") is False
    assert is_safe_url("not a url") is False


def test_ingest_document_chunks_embeds_and_stores(qdrant):
    doc_id = uuid.uuid4()
    user_id = uuid.uuid4()
    text = ("Paragraph one. " * 50) + "\n\n" + ("Paragraph two. " * 50)

    count = ingest_document(qdrant, FakeEmbedder(), doc_id, user_id, text)

    assert count > 0
    stored = qdrant.scroll(collection_name=RAG_CHUNKS, limit=100)[0]
    assert len(stored) == count
    assert all(p.payload["document_id"] == str(doc_id) for p in stored)
    assert all(p.payload["user_id"] == str(user_id) for p in stored)


def test_ingest_document_empty_text_returns_zero(qdrant):
    count = ingest_document(qdrant, FakeEmbedder(), uuid.uuid4(), uuid.uuid4(), "")
    assert count == 0


def test_ingest_document_stamps_title_and_source(qdrant):
    doc_id = uuid.uuid4()
    user_id = uuid.uuid4()
    count = ingest_document(
        qdrant,
        FakeEmbedder(),
        doc_id,
        user_id,
        "Some fairly long piece of content to chunk. " * 20,
        document_title="My Doc",
        source_url="https://example.com",
        valid_until="2027-01-01T00:00:00",
    )
    assert count > 0
    stored = qdrant.scroll(collection_name=RAG_CHUNKS, limit=100)[0]
    assert all(p.payload["document_title"] == "My Doc" for p in stored)
    assert all(p.payload["source_url"] == "https://example.com" for p in stored)
    assert all(p.payload["valid_until"] == "2027-01-01T00:00:00" for p in stored)
