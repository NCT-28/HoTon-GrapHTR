"""Adaptive routing: classify query complexity to decide how much retrieval work to do."""

from enum import Enum


class QueryComplexity(str, Enum):
    DIRECT = "direct"
    SINGLE = "single"
    MULTI = "multi"


_ROUTING_PROMPT = """Classify the complexity of this user query for a retrieval-augmented assistant.
Respond with exactly one word: "direct", "single", or "multi".
- "direct": a greeting, chit-chat, or general-knowledge question needing no lookup of the user's own documents/memories.
- "single": a question answerable by one retrieval pass over the user's documents/memories.
- "multi": a complex question requiring multiple retrieval steps or reasoning over several sub-questions.

Query: {query}
Classification:"""


def classify_query(llm, query: str) -> QueryComplexity:
    raw = llm.generate(_ROUTING_PROMPT.format(query=query), max_new_tokens=10, temperature=0.0).strip().lower()

    # Exact first-word match first, since the prompt asks for exactly one word
    # and this can't be fooled by a longer explanation containing "direct" as
    # a substring (e.g. "not a direct lookup, needs multi-step reasoning").
    first_word = raw.split()[0].strip(".,!?\"'") if raw.split() else ""
    for complexity in QueryComplexity:
        if complexity.value == first_word:
            return complexity

    # Fall back to substring match, preferring the longest/most-specific label
    # so "multi" wins over "direct" when both appear in a rambling response.
    matches = [c for c in QueryComplexity if c.value in raw]
    if matches:
        return max(matches, key=lambda c: len(c.value))

    return QueryComplexity.SINGLE  # unparsable output fails safe to "still retrieve", never silently skips RAG
