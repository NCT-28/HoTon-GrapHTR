import httpx

from app.rag.documents import is_safe_url


class BrowserClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    async def get_page_text(self, url: str) -> str:
        # SSRF check enforced here too, not just at the one current caller
        # (app/rag/documents.py's URL-ingest route) — any future caller of
        # BrowserClient must not be able to forward an unchecked URL to the
        # browser microservice.
        if not is_safe_url(url):
            raise ValueError("URL targets a blocked or private address")
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(f"{self.base_url}/navigate", json={"url": url})
            resp.raise_for_status()
            return resp.json()["text"]
