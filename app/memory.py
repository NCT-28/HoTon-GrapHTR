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
