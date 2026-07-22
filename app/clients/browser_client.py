import httpx


class BrowserClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    async def get_page_text(self, url: str) -> str:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(f"{self.base_url}/navigate", json={"url": url})
            resp.raise_for_status()
            return resp.json()["text"]
