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
    for complexity in QueryComplexity:
        if complexity.value in raw:
            return complexity
    return QueryComplexity.SINGLE  # unparsable output fails safe to "still retrieve", never silently skips RAG
