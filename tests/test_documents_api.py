import asyncio
import io
import uuid

from fastapi.testclient import TestClient

import app.rag.documents as documents_module
from app.main import create_app


class FakeEmbedder:
    dim = 384

    def embed_batch(self, texts):
        return [[0.1] * 384 for _ in texts]


class FakeLLM:
    def generate(self, prompt, max_new_tokens=256, temperature=0.1):
        return "[]"


class EntityExtractingLLM:
    """Returns a real entity for '... Retriever ...' text, matching
    entity_extraction.py's expected prompt/response shape (see
    tests/test_graph_pipeline.py)."""

    def generate(self, prompt, max_new_tokens=256, temperature=0.1):
        if "Extract 0-5 named entities" in prompt:
            return '{"entities": [{"name": "Retriever", "type": "concept"}], "relationships": []}'
        return "no"


def make_client(qdrant, graph_store):
    app = create_app(qdrant_client=qdrant, embedder=FakeEmbedder(), llm=FakeLLM(), graph_store=graph_store)
    return TestClient(app)


def test_upload_document_dispatches_entity_extraction_via_to_thread(qdrant, graph_store, monkeypatch):
    """entity_extraction's llm.generate() call is synchronous with no `await`
    inside it -- asyncio.create_task() alone would still run it on the event
    loop thread and freeze every other request until it's done (this hung the
    real server in production). It must go through asyncio.to_thread so the
    blocking work lands on a worker thread instead."""
    real_to_thread = asyncio.to_thread
    calls = []

    async def recording_to_thread(func, *args, **kwargs):
        calls.append(func)
        return await real_to_thread(func, *args, **kwargs)

    monkeypatch.setattr(documents_module.asyncio, "to_thread", recording_to_thread)

    app = create_app(qdrant_client=qdrant, embedder=FakeEmbedder(), llm=EntityExtractingLLM(), graph_store=graph_store)
    client = TestClient(app)
    user_id = str(uuid.uuid4())

    resp = client.post(
        "/api/documents",
        files={"file": ("notes.txt", ("Text about a Retriever. " * 10).encode(), "text/plain")},
        headers={"X-User-Id": user_id},
    )

    assert resp.status_code == 202
    assert calls == [documents_module._run_entity_extraction_in_thread], (
        "entity extraction was not dispatched via asyncio.to_thread"
    )
    # Confirms _run_entity_extraction_in_thread's asyncio.run(...) wrapper
    # still drives the extraction to completion and stores results correctly.
    assert graph_store.list_text_entities(user_id), "entity extraction did not store any entities"


def test_upload_document_then_appears_in_list(qdrant, graph_store):
    client = make_client(qdrant, graph_store)
    user_id = str(uuid.uuid4())

    resp = client.post(
        "/api/documents",
        files={"file": ("notes.txt", io.BytesIO(b"hello world " * 100), "text/plain")},
        headers={"X-User-Id": user_id},
    )
    assert resp.status_code == 202
    doc_id = resp.json()["document_id"]

    listed = client.get("/api/documents", headers={"X-User-Id": user_id})
    assert listed.status_code == 200
    docs = listed.json()["documents"]
    assert any(d["id"] == doc_id for d in docs)
    assert next(d for d in docs if d["id"] == doc_id)["status"] == "ready"


def test_get_document_not_found(qdrant, graph_store):
    client = make_client(qdrant, graph_store)
    user_id = str(uuid.uuid4())
    resp = client.get(f"/api/documents/{uuid.uuid4()}", headers={"X-User-Id": user_id})
    assert resp.status_code == 404


def test_delete_document(qdrant, graph_store):
    client = make_client(qdrant, graph_store)
    user_id = str(uuid.uuid4())
    resp = client.post(
        "/api/documents",
        files={"file": ("notes.txt", io.BytesIO(b"content"), "text/plain")},
        headers={"X-User-Id": user_id},
    )
    doc_id = resp.json()["document_id"]

    delete_resp = client.delete(f"/api/documents/{doc_id}", headers={"X-User-Id": user_id})
    assert delete_resp.status_code == 204

    get_resp = client.get(f"/api/documents/{doc_id}", headers={"X-User-Id": user_id})
    assert get_resp.status_code == 404


def test_list_documents_records_usage(qdrant, graph_store, usage_store):
    app = create_app(
        qdrant_client=qdrant, embedder=FakeEmbedder(), llm=FakeLLM(), graph_store=graph_store, usage_store=usage_store,
    )
    client = TestClient(app)
    client.get("/api/documents", headers={"X-User-Id": str(uuid.uuid4())})

    assert any(e["tool_name"] == "list_documents" for e in usage_store.events)
