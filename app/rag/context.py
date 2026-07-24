"""Context assembly."""

from app.rag.memory import RetrievedMemory
from app.rag.retrieval import RetrievedChunk


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


# --- profile context + full assembly ---

from app.rag.profile import UserProfile


def build_profile_context_section(profile: UserProfile) -> str:
    if profile.level == "unknown" and profile.style == "neutral" and profile.preferred_lang == "auto":
        return ""

    lines = [
        "[User Context]",
        f"Expertise level: {profile.level}",
        f"Communication style: {profile.style}",
        f"Language: {profile.preferred_lang}",
    ]
    if profile.project_context:
        lines.append(f"Current project: {profile.project_context}")
    lines.append("Adapt your responses accordingly.")
    return "\n".join(lines)


def build_graph_context_section(graph_nodes: list[dict], graph_edges: list[dict]) -> str:
    if not graph_nodes:
        return ""

    name_by_id = {n["id"]: n["name"] for n in graph_nodes}
    edges_by_source: dict[str, list[dict]] = {}
    for e in graph_edges:
        edges_by_source.setdefault(e["source"], []).append(e)

    sections = ["[Code Graph Context]", "─" * 30]
    for n in graph_nodes:
        kind = f" ({n['kind']})" if n.get("kind") else ""
        location = f" — {n['file_path']}:{n['start_line']}" if n.get("file_path") and n.get("start_line") is not None else ""
        sections.append(f"{n['name']}{kind}{location}")
        for e in edges_by_source.get(n["id"], []):
            target_name = name_by_id.get(e["target"], e["target"])
            sections.append(f"  --{e['type']}--> {target_name}")

    sections.append("─" * 30)
    return "\n".join(sections)


def build_full_context(
    chunks: list[RetrievedChunk],
    memories: list[RetrievedMemory],
    profile: UserProfile,
    graph_nodes: list[dict] | None = None,
    graph_edges: list[dict] | None = None,
) -> str:
    parts = []
    profile_section = build_profile_context_section(profile)
    if profile_section:
        parts.append(profile_section)
    if memories:
        parts.append(build_memory_context_section(memories))
    if chunks:
        parts.append(build_rag_context_section(chunks))
    graph_section = build_graph_context_section(graph_nodes or [], graph_edges or [])
    if graph_section:
        parts.append(graph_section)
    return "\n\n".join(parts)
