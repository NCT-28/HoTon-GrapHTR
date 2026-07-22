"""CRAG: grade retrieved-chunk relevance; fall back to web search when it's poor."""

import httpx

from app.rag.retrieval import RetrievedChunk

CRAG_RELEVANCE_THRESHOLD = 0.5

_GRADE_PROMPT = """Rate how relevant this retrieved passage is to answering the question, on a scale 0.0-1.0.
Respond with only a number.

Question: {query}
Passage: {passage}
Relevance score:"""


def grade_relevance(llm, query: str, passage: str) -> float:
    raw = llm.generate(_GRADE_PROMPT.format(query=query, passage=passage), max_new_tokens=10, temperature=0.0).strip()
    try:
        score = float(raw.split()[0])
    except (ValueError, IndexError):
        return 0.5  # unparsable grading output: treat as neutral, neither forces nor blocks fallback alone
    return max(0.0, min(score, 1.0))


async def searxng_web_search(
    client: httpx.AsyncClient, searxng_url: str, query: str, max_results: int = 3
) -> list[str]:
    resp = await client.get(f"{searxng_url.rstrip('/')}/search", params={"q": query, "format": "json"})
    resp.raise_for_status()
    data = resp.json()
    return [r["content"] for r in data.get("results", [])[:max_results] if r.get("content")]


async def crag_correct(llm, web_search_fn, query: str, chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
    if not chunks:
        avg_score = 0.0
    else:
        scores = [grade_relevance(llm, query, c.content) for c in chunks]
        avg_score = sum(scores) / len(scores)

    if avg_score >= CRAG_RELEVANCE_THRESHOLD:
        return chunks

    snippets = await web_search_fn(query)
    web_chunks = [
        RetrievedChunk(
            id=f"web-{i}", content=snippet, document_title="Web Search", source_url=None,
            similarity=avg_score, document_expired=False,
        )
        for i, snippet in enumerate(snippets)
    ]
    return chunks + web_chunks
