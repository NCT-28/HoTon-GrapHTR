"""Context assembly — ported from hoton-lmr/src/rag/context.rs and src/prompts/context.rs."""

from app.memory import RetrievedMemory
from app.retrieval import RetrievedChunk


def _self_consistency_adjustment(memory: RetrievedMemory, query: str) -> float:
    content_lower = memory.content.lower()
    query_len = len(query)
    factor = 1.0

    if query_len > 300 and any(w in content_lower for w in ("short", "brief", "concise")):
        factor *= 0.6

    if query_len < 20 and any(w in content_lower for w in ("detail", "thorough", "in-depth")):
        factor *= 0.7

    return factor


def apply_self_consistency(memories: list[RetrievedMemory], query: str) -> None:
    """Mutates `memories` in place: adjusts confidence, drops entries below 0.3, re-sorts descending."""
    for mem in memories:
        mem.confidence *= _self_consistency_adjustment(mem, query)

    memories[:] = [m for m in memories if m.confidence >= 0.3]
    memories.sort(key=lambda m: m.confidence, reverse=True)


def build_rag_context_section(chunks: list[RetrievedChunk]) -> str:
    if not chunks:
        return ""

    sections = ["[Knowledge Context]", "─" * 30]
    for chunk in chunks:
        source = f"{chunk.document_title} ({chunk.source_url})" if chunk.source_url else chunk.document_title
        sections.append(f"Source: {source}")
        sections.append(chunk.content)
        sections.append("")

    sections.append("─" * 30)
    sections.append("Answer based on the above sources when relevant.")
    sections.append("Cite sources as [Source: title] inline.")
    return "\n".join(sections)


def build_memory_context_section(memories: list[RetrievedMemory]) -> str:
    if not memories:
        return ""

    sections = ["[What I know about you]"]
    sections.extend(f"- {m.content}" for m in memories)
    return "\n".join(sections)


def build_full_rag_context(chunks: list[RetrievedChunk], memories: list[RetrievedMemory]) -> str:
    parts = []
    if memories:
        parts.append(build_memory_context_section(memories))
    if chunks:
        parts.append(build_rag_context_section(chunks))
    return "\n\n".join(parts)
