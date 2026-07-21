"""HyDE: generate a hypothetical answer and embed that instead of the raw query,
closing the semantic gap between short/vague questions and longer document text."""

_HYDE_PROMPT = """Write a short hypothetical passage (2-4 sentences) that would directly answer this question, \
as if it came from the user's own notes or documents. Do not mention that it is hypothetical.

Question: {query}
Passage:"""


def generate_hypothetical_answer(llm, query: str) -> str:
    return llm.generate(_HYDE_PROMPT.format(query=query), max_new_tokens=150, temperature=0.3).strip()
