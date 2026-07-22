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


# --- REST layer ---

import asyncio
import datetime
import uuid as _uuid

from fastapi import APIRouter, File, Header, HTTPException, UploadFile, status
from qdrant_client.models import FieldCondition, Filter, MatchValue, PointIdsList

from app.graph_pipeline import run_entity_extraction_and_linking
from app.qdrant_store import RAG_DOCUMENTS


def build_documents_router(get_client, get_embedder, get_graph_store=None, get_llm=None) -> APIRouter:
    router = APIRouter()

    @router.post("/api/documents", status_code=status.HTTP_202_ACCEPTED)
    async def upload_document(
        file: UploadFile = File(...),
        x_user_id: str = Header(...),
    ):
        data = await file.read()
        title, text = process_uploaded_file(file.filename, file.content_type, data)

        doc_id = _uuid.uuid4()
        client = get_client()
        embedder = get_embedder()

        chunk_count = ingest_document(
            client, embedder, doc_id, _uuid.UUID(x_user_id), text,
            document_title=title, source_url=None, valid_until=None,
        )

        client.upsert(
            collection_name=RAG_DOCUMENTS,
            points=[
                PointStruct(
                    id=str(doc_id),
                    vector=[0.0],
                    payload={
                        "user_id": x_user_id,
                        "title": title,
                        "source_type": "file",
                        "source_url": None,
                        "file_name": file.filename,
                        "status": "ready",
                        "error_msg": None,
                        "chunk_count": chunk_count,
                        "created_at": datetime.datetime.utcnow().isoformat(),
                    },
                )
            ],
            wait=True,
        )

        if get_graph_store is not None and get_llm is not None:
            asyncio.create_task(
                run_entity_extraction_and_linking(get_graph_store(), get_llm(), embedder, x_user_id, str(doc_id), text)
            )

        return {"document_id": str(doc_id)}

    @router.get("/api/documents")
    async def list_documents(x_user_id: str = Header(...)):
        client = get_client()
        points, _ = client.scroll(
            collection_name=RAG_DOCUMENTS,
            scroll_filter=Filter(must=[FieldCondition(key="user_id", match=MatchValue(value=x_user_id))]),
            limit=1000,
        )
        docs = [{"id": p.id, **p.payload} for p in points]
        docs.sort(key=lambda d: d["created_at"], reverse=True)
        return {"documents": docs}

    @router.get("/api/documents/{document_id}")
    async def get_document(document_id: str, x_user_id: str = Header(...)):
        client = get_client()
        points = client.retrieve(collection_name=RAG_DOCUMENTS, ids=[document_id])
        if not points or points[0].payload.get("user_id") != x_user_id:
            raise HTTPException(status_code=404, detail="Document not found")
        return {"id": points[0].id, **points[0].payload}

    @router.delete("/api/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_document(document_id: str, x_user_id: str = Header(...)):
        client = get_client()
        points = client.retrieve(collection_name=RAG_DOCUMENTS, ids=[document_id])
        if not points or points[0].payload.get("user_id") != x_user_id:
            raise HTTPException(status_code=404, detail="Document not found")

        client.delete(collection_name=RAG_DOCUMENTS, points_selector=PointIdsList(points=[document_id]))
        client.delete(
            collection_name=RAG_CHUNKS,
            points_selector=Filter(must=[FieldCondition(key="document_id", match=MatchValue(value=document_id))]),
        )
        return None

    return router


# --- URL ingest ---

from pydantic import BaseModel


class UrlDocumentRequest(BaseModel):
    url: str
    title: str | None = None


def add_url_route(router: APIRouter, get_client, get_embedder, get_browser_client, get_graph_store=None, get_llm=None) -> None:
    @router.post("/api/documents/url", status_code=status.HTTP_202_ACCEPTED)
    async def upload_url_document(payload: UrlDocumentRequest, x_user_id: str = Header(...)):
        if not payload.url:
            raise HTTPException(status_code=400, detail="URL is required")
        if not payload.url.startswith(("http://", "https://")):
            raise HTTPException(status_code=400, detail="Invalid URL format")
        if not is_safe_url(payload.url):
            raise HTTPException(status_code=400, detail="URL targets a blocked or private address")

        browser_client = get_browser_client()
        text = await browser_client.get_page_text(payload.url)

        doc_id = _uuid.uuid4()
        client = get_client()
        embedder = get_embedder()
        chunk_count = ingest_document(
            client, embedder, doc_id, _uuid.UUID(x_user_id), text,
            document_title=payload.title or "Web Document", source_url=payload.url, valid_until=None,
        )

        client.upsert(
            collection_name=RAG_DOCUMENTS,
            points=[
                PointStruct(
                    id=str(doc_id),
                    vector=[0.0],
                    payload={
                        "user_id": x_user_id,
                        "title": payload.title or "Web Document",
                        "source_type": "url",
                        "source_url": payload.url,
                        "file_name": None,
                        "status": "ready",
                        "error_msg": None,
                        "chunk_count": chunk_count,
                        "created_at": datetime.datetime.utcnow().isoformat(),
                    },
                )
            ],
            wait=True,
        )

        if get_graph_store is not None and get_llm is not None:
            asyncio.create_task(
                run_entity_extraction_and_linking(get_graph_store(), get_llm(), embedder, x_user_id, str(doc_id), text)
            )

        return {"document_id": str(doc_id)}
