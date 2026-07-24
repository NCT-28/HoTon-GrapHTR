"""Reduce a natural-language query to candidate code-symbol names, since
bfs_query matches by substring against a node's `name` field, not free text."""

import json

_KEYWORD_PROMPT = """Given this question, list 0-3 code symbol, class, or function names that \
might be directly relevant to answering it, based on typical naming conventions. \
Respond as a JSON array of strings, e.g. ["retrieve_chunks", "Embedder"]. If none are relevant, respond [].

Question: {query}
JSON:"""


def extract_graph_keywords(llm, query: str) -> list[str]:
    raw = llm.generate(_KEYWORD_PROMPT.format(query=query), max_new_tokens=60, temperature=0.0)
    start, end = raw.find("["), raw.rfind("]")
    if start == -1 or end == -1 or end < start:
        return []
    try:
        parsed = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [k for k in parsed if isinstance(k, str) and k.strip()][:3]
