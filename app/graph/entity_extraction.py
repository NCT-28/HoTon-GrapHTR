"""Text-entity + relationship extraction for the Phase 2 graph cross-link —
mirrors app/memory.py's extraction pattern (LLM prompt -> JSON parse ->
validate) but targets named entities/relationships instead of facts."""

import json
import uuid as _uuid
from dataclasses import dataclass

_ENTITY_EXTRACTION_TEMPLATE = """Extract 0-5 named entities (concepts, people, organizations, technologies) \
from this text, and any direct relationships between them.
Output JSON: {{"entities": [{{"name": "...", "type": "concept|person|org|technology|other"}}], \
"relationships": [{{"source": "...", "target": "..."}}]}}
Only include relationships where both names appear in the entities list.
If nothing notable, output: {{"entities": [], "relationships": []}}

<text>{snippet}</text>"""


@dataclass
class ExtractedEntity:
    id: str
    name: str
    entity_type: str


@dataclass
class ExtractedRelationship:
    source_name: str
    target_name: str


def _snippet(text: str) -> str:
    return text[:2000].replace("</text>", "&lt;/text&gt;")


def parse_entities_from_text(text: str) -> tuple[list[ExtractedEntity], list[ExtractedRelationship]]:
    raw = text.strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1 or end < start:
        return [], []
    try:
        parsed = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return [], []

    entities = [
        ExtractedEntity(id=str(_uuid.uuid4()), name=e["name"], entity_type=e.get("type", "other"))
        for e in parsed.get("entities", [])
        if e.get("name")
    ]
    known_names = {e.name for e in entities}
    relationships = [
        ExtractedRelationship(source_name=r["source"], target_name=r["target"])
        for r in parsed.get("relationships", [])
        if r.get("source") in known_names and r.get("target") in known_names
    ]
    return entities, relationships


def extract_and_store_entities(graph_store, llm, user_id: str, source_doc_id: str, text: str) -> int:
    """Extract entities/relationships from ingested document text and store
    them in the graph. Returns the number of entities stored."""
    prompt = _ENTITY_EXTRACTION_TEMPLATE.format(snippet=_snippet(text))
    raw_text = llm.generate(prompt, max_new_tokens=400, temperature=0.1)
    entities, relationships = parse_entities_from_text(raw_text)
    if not entities:
        return 0

    # Map each name to *all* matching entity ids (not just the last one seen) —
    # the LLM can extract two distinct entities sharing a name, and collapsing
    # them into a single id would silently mislink relationship edges.
    name_to_ids: dict[str, list[str]] = {}
    for e in entities:
        name_to_ids.setdefault(e.name, []).append(e.id)

    graph_store.upsert_text_entities([
        {"id": e.id, "user_id": user_id, "name": e.name, "entity_type": e.entity_type,
         "source_doc_id": source_doc_id, "source_memory_id": None}
        for e in entities
    ])
    graph_store.upsert_related_edges([
        {"source": source_id, "target": target_id}
        for r in relationships
        for source_id in name_to_ids[r.source_name]
        for target_id in name_to_ids[r.target_name]
    ])
    return len(entities)
