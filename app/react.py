"""Multi-step ReAct planning: bounded retrieve/decide loop for complex queries.
Mirrors hoton-lmr's tool_loop.rs MAX_TOOL_CALLS pattern."""

import json
import uuid

from app.memory import RetrievedMemory, retrieve_memories
from app.retrieval import RetrievedChunk, retrieve_chunks

MAX_STEPS = 5

_CONTINUE_PROMPT = """Given the question and the passages retrieved so far, decide if there is enough \
information to answer well.
Respond with JSON only: {{"enough": true|false, "next_query": "..."}}
If enough is true, next_query can be null.

Question: {query}
Passages:
{passages}
JSON:"""


def decide_continue(llm, query: str, chunks: list[RetrievedChunk]) -> tuple[bool, str | None]:
    passages = "\n---\n".join(c.content for c in chunks) or "(none)"
    raw = llm.generate(_CONTINUE_PROMPT.format(query=query, passages=passages), max_new_tokens=100, temperature=0.0)
    try:
        start, end = raw.index("{"), raw.rindex("}")
        data = json.loads(raw[start : end + 1])
        return bool(data.get("enough", True)), data.get("next_query")
    except (ValueError, json.JSONDecodeError):
        return True, None  # unparsable: stop rather than risk looping on garbage forever


def run_multi_step_retrieval(
    client, embedder, llm, user_id: uuid.UUID, query: str, top_k: int, min_similarity: float
) -> tuple[list[RetrievedChunk], list[RetrievedMemory]]:
    all_chunks: list[RetrievedChunk] = []
    all_memories: list[RetrievedMemory] = []
    seen_chunk_ids: set[str] = set()
    seen_memory_ids: set[str] = set()
    current_query = query

    for _ in range(MAX_STEPS):
        chunks = retrieve_chunks(client, embedder, user_id, current_query, top_k, min_similarity)
        memories = retrieve_memories(client, embedder, user_id, current_query, top_k, min_similarity)

        for c in chunks:
            if c.id not in seen_chunk_ids:
                seen_chunk_ids.add(c.id)
                all_chunks.append(c)
        for m in memories:
            if m.id not in seen_memory_ids:
                seen_memory_ids.add(m.id)
                all_memories.append(m)

        enough, next_query = decide_continue(llm, query, all_chunks)
        if enough or not next_query:
            break
        current_query = next_query

    return all_chunks, all_memories
