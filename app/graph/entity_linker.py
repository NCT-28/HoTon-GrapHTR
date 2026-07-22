"""Links newly extracted TextEntity nodes to existing CodeSymbol nodes: the
embedder narrows candidates by name/context similarity (cheap), then the
local reasoning LLM confirms which of those few candidates are a real match
(expensive, so only run on a small shortlist, not the whole repo)."""

CANDIDATE_TOP_N = 3

_LINK_CONFIRM_TEMPLATE = """Does the concept "{entity_name}" refer to the code symbol "{symbol_name}" \
(a {symbol_kind} in {file_path})? Answer with a single word: yes or no.

Concept: {entity_name}
Code symbol: {symbol_name}"""


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _confirm_match(llm, entity_name: str, symbol: dict) -> bool:
    prompt = _LINK_CONFIRM_TEMPLATE.format(
        entity_name=entity_name, symbol_name=symbol["name"], symbol_kind=symbol["kind"], file_path=symbol["file_path"]
    )
    answer = llm.generate(prompt, max_new_tokens=5, temperature=0.0).strip().lower()
    return answer.startswith("yes")


def link_entities_to_code(graph_store, llm, embedder, user_id: str, source_doc_id: str) -> int:
    """For every TextEntity extracted from `source_doc_id`, find up to
    CANDIDATE_TOP_N candidate CodeSymbols by embedding similarity and ask the
    LLM to confirm each. Returns the number of MENTIONS edges created."""
    entities = [e for e in graph_store.list_text_entities(user_id) if e["source_doc_id"] == source_doc_id]
    if not entities:
        return 0

    symbols = graph_store.list_code_symbols(user_id)
    if not symbols:
        return 0

    symbol_vectors = embedder.embed_batch([f"{s['kind']} {s['name']}" for s in symbols])

    mentions_edges: list[dict] = []
    for entity in entities:
        entity_vector = embedder.embed_single(entity["name"])
        candidates = sorted(
            zip(symbols, symbol_vectors), key=lambda pair: -_cosine_similarity(entity_vector, pair[1])
        )[:CANDIDATE_TOP_N]

        for symbol, _vector in candidates:
            if _confirm_match(llm, entity["name"], symbol):
                mentions_edges.append({"source": entity["id"], "target": symbol["id"]})

    graph_store.upsert_mentions_edges(mentions_edges)
    return len(mentions_edges)
