import uuid

from fastapi.testclient import TestClient

from app.browser_client import BrowserClient
from app.main import create_app


class FakeEmbedder:
    dim = 384

    def embed_batch(self, texts):
        return [[0.1] * 384 for _ in texts]


class FakeBrowserClient:
    async def get_page_text(self, url: str) -> str:
        return f"page content for {url} " * 20


def test_browser_client_calls_expected_endpoint():
    # BrowserClient.get_page_text posts to {base_url}/navigate and reads `.text`
    client = BrowserClient(base_url="http://browser:8090")
    assert client.base_url == "http://browser:8090"


def test_upload_url_rejects_private_targets(qdrant):
    app = create_app(qdrant_client=qdrant, embedder=FakeEmbedder(), browser_client=FakeBrowserClient())
    client = TestClient(app)
    resp = client.post(
        "/api/documents/url",
        json={"url": "http://localhost/secret"},
        headers={"X-User-Id": str(uuid.uuid4())},
    )
    assert resp.status_code == 400


def test_upload_url_ingests_page_text(qdrant):
    app = create_app(qdrant_client=qdrant, embedder=FakeEmbedder(), browser_client=FakeBrowserClient())
    client = TestClient(app)
    user_id = str(uuid.uuid4())
    resp = client.post(
        "/api/documents/url",
        json={"url": "https://example.com/article", "title": "Article"},
        headers={"X-User-Id": user_id},
    )
    assert resp.status_code == 202
    doc_id = resp.json()["document_id"]

    got = client.get(f"/api/documents/{doc_id}", headers={"X-User-Id": user_id})
    assert got.status_code == 200
    assert got.json()["status"] == "ready"
    assert got.json()["chunk_count"] > 0
