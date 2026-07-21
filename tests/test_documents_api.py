import io
import uuid

from fastapi.testclient import TestClient

from app.main import create_app


class FakeEmbedder:
    dim = 384

    def embed_batch(self, texts):
        return [[0.1] * 384 for _ in texts]


def make_client(qdrant):
    app = create_app(qdrant_client=qdrant, embedder=FakeEmbedder())
    return TestClient(app)


def test_upload_document_then_appears_in_list(qdrant):
    client = make_client(qdrant)
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


def test_get_document_not_found(qdrant):
    client = make_client(qdrant)
    user_id = str(uuid.uuid4())
    resp = client.get(f"/api/documents/{uuid.uuid4()}", headers={"X-User-Id": user_id})
    assert resp.status_code == 404


def test_delete_document(qdrant):
    client = make_client(qdrant)
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
