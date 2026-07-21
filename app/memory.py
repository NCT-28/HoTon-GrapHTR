"""Memory extraction, classification, and storage — ported from hoton-lmr/src/rag/memory.rs."""

import json
from dataclasses import dataclass
from enum import Enum

USER_ASSERTION_CONFIDENCE_MAX = 0.7
OPINION_CONFIDENCE_MAX = 0.5

_INJECTION_PATTERNS = [
    "ignore previous", "forget all", "you are now", "disregard",
    "override instructions", "[system]", "###inst###", "</system>",
    "<|system|>", "system prompt", "<|im_start|>", "[inst]", "<<sys>>",
]
_SENSITIVE_PATTERNS = [
    "password", "api key", "api secret", "client secret", "secret key",
    "secret token", "private key", "api token", "auth token", "access token",
    "bearer token", "credential", "-----begin", " sk-",
]
_OPINION_PATTERNS = [
    " better than", " worse than", "is the best", "is better", "is worse",
    "should use", "you should",
]


class ClaimClass(str, Enum):
    FACT = "fact"
    OPINION = "opinion"
    SENSITIVE = "sensitive"
    INJECTION = "injection"


class MemoryType(str, Enum):
    FACT = "fact"
    PREFERENCE = "preference"
    CORRECTION = "correction"


class MemorySource(str, Enum):
    EXPLICIT = "explicit"
    IMPLICIT = "implicit"
    INFERRED = "inferred"


@dataclass
class ExtractedMemory:
    content: str
    memory_type: MemoryType
    confidence: float
    source: MemorySource
    claim_class: ClaimClass


def classify_claim(content: str) -> ClaimClass:
    """Priority: Injection > Sensitive > Opinion > Fact."""
    lower = content.lower()

    if any(p in lower for p in _INJECTION_PATTERNS):
        return ClaimClass.INJECTION
    if any(p in lower for p in _SENSITIVE_PATTERNS):
        return ClaimClass.SENSITIVE
    if any(p in lower for p in _OPINION_PATTERNS):
        return ClaimClass.OPINION
    return ClaimClass.FACT


def parse_memories_from_text(text: str) -> list[ExtractedMemory]:
    raw = text.strip()
    start, end = raw.find("["), raw.rfind("]")
    if start == -1 or end == -1 or end < start:
        return []
    json_str = raw[start : end + 1]

    try:
        raw_memories = json.loads(json_str)
    except json.JSONDecodeError:
        return []

    results: list[ExtractedMemory] = []
    for rm in raw_memories:
        type_str = rm.get("type")
        try:
            memory_type = MemoryType(type_str)
        except ValueError:
            continue

        source = (
            MemorySource.EXPLICIT
            if memory_type in (MemoryType.CORRECTION, MemoryType.PREFERENCE)
            else MemorySource.INFERRED
        )

        content = rm.get("content", "")
        claim_class = classify_claim(content)
        if claim_class in (ClaimClass.INJECTION, ClaimClass.SENSITIVE):
            continue

        ceiling = OPINION_CONFIDENCE_MAX if claim_class == ClaimClass.OPINION else USER_ASSERTION_CONFIDENCE_MAX
        confidence = max(0.0, min(rm.get("confidence", 0.5), ceiling))

        results.append(
            ExtractedMemory(
                content=content,
                memory_type=memory_type,
                confidence=confidence,
                source=source,
                claim_class=claim_class,
            )
        )

    return results


# --- extraction + storage ---

import datetime
import uuid as _uuid

from qdrant_client import QdrantClient
from qdrant_client.models import FieldCondition, Filter, MatchValue, PointStruct

from app.qdrant_store import USER_MEMORIES

_EXTRACTION_TEMPLATE = """Extract 0-3 concise facts or preferences from this conversation turn.
Only extract information that would be useful to remember long-term.
Write the "content" field in the same language as the User's message.
Output JSON array: [{{"content": "...", "type": "fact|preference|correction", "confidence": 0.0-1.0}}]
Rate confidence based on how clearly and explicitly the information was stated (1.0 = stated directly, 0.5 = implied, 0.3 = uncertain).
If nothing notable, output: []

<user_turn>{user_snippet}</user_turn>
<assistant_turn>{assistant_snippet}</assistant_turn>"""


def _snippet(message: str) -> str:
    return (
        message[:1000]
        .replace("</user_turn>", "&lt;/user_turn&gt;")
        .replace("</assistant_turn>", "&lt;/assistant_turn&gt;")
    )


def deprecate_conflicting_memories(
    client: QdrantClient, user_id: _uuid.UUID, embedding: list[float], threshold: float = 0.85
) -> int:
    """Mark existing active memories with high embedding similarity as deprecated.
    Called before inserting a Correction memory (contrastive update)."""
    hits = client.query_points(
        collection_name=USER_MEMORIES,
        query=embedding,
        query_filter=Filter(
            must=[
                FieldCondition(key="user_id", match=MatchValue(value=str(user_id))),
                FieldCondition(key="status", match=MatchValue(value="active")),
            ]
        ),
        score_threshold=threshold,
        limit=100,
        with_payload=False,
    ).points

    if not hits:
        return 0

    for hit in hits:
        client.set_payload(collection_name=USER_MEMORIES, payload={"status": "deprecated"}, points=[hit.id])
    return len(hits)


def extract_and_store_memories(
    client: QdrantClient,
    embedder,
    llm,
    user_id: _uuid.UUID,
    user_message: str,
    assistant_message: str,
) -> int:
    """Extract facts/preferences from a conversation turn and store them. Returns count stored."""
    prompt = _EXTRACTION_TEMPLATE.format(
        user_snippet=_snippet(user_message), assistant_snippet=_snippet(assistant_message)
    )
    raw_text = llm.generate(prompt, max_new_tokens=256, temperature=0.1)
    memories = parse_memories_from_text(raw_text)

    stored = 0
    now_iso = datetime.datetime.utcnow().isoformat()
    for mem in memories:
        embedding = embedder.embed_single(mem.content)

        if mem.memory_type == MemoryType.CORRECTION:
            deprecate_conflicting_memories(client, user_id, embedding)

        client.upsert(
            collection_name=USER_MEMORIES,
            points=[
                PointStruct(
                    id=str(_uuid.uuid4()),
                    vector=embedding,
                    payload={
                        "user_id": str(user_id),
                        "content": mem.content,
                        "memory_type": mem.memory_type.value,
                        "confidence": mem.confidence,
                        "source": mem.source.value,
                        "claim_class": mem.claim_class.value,
                        "status": "active",
                        "last_used_at": now_iso,
                        "created_at": now_iso,
                    },
                )
            ],
            wait=True,
        )
        stored += 1

    return stored


# --- retrieval ---

import math
from dataclasses import dataclass as _dataclass


@_dataclass
class RetrievedMemory:
    id: str
    content: str
    memory_type: str
    confidence: float


def retrieve_memories(
    client: QdrantClient,
    embedder,
    user_id: _uuid.UUID,
    query: str,
    top_k: int,
    min_similarity: float,
) -> list[RetrievedMemory]:
    query_vector = embedder.embed_single(query)

    hits = client.query_points(
        collection_name=USER_MEMORIES,
        query=query_vector,
        query_filter=Filter(
            must=[
                FieldCondition(key="user_id", match=MatchValue(value=str(user_id))),
                FieldCondition(key="status", match=MatchValue(value="active")),
            ]
        ),
        limit=top_k * 3,
        with_payload=True,
    ).points

    now = datetime.datetime.utcnow()
    scored = []
    for hit in hits:
        if hit.score < min_similarity:
            continue
        payload = hit.payload
        last_used_at = datetime.datetime.fromisoformat(payload["last_used_at"])
        age_days = (now - last_used_at).total_seconds() / 86400.0
        effective_confidence = payload["confidence"] * math.exp(-0.01 * age_days)
        if effective_confidence < 0.3:
            continue
        scored.append((effective_confidence, hit.score, hit.id, payload))

    scored.sort(key=lambda t: (-t[0], -t[1]))
    top = scored[:top_k]

    if top:
        now_iso = now.isoformat()
        for _eff, _score, point_id, _payload in top:
            client.set_payload(collection_name=USER_MEMORIES, payload={"last_used_at": now_iso}, points=[point_id])

    return [
        RetrievedMemory(id=str(point_id), content=payload["content"], memory_type=payload["memory_type"], confidence=eff)
        for eff, _score, point_id, payload in top
    ]
