"""User profile management — ported from hoton-lmr/src/rag/profile.rs."""

import datetime
import uuid
from dataclasses import dataclass, field

from qdrant_client import QdrantClient
from qdrant_client.models import FieldCondition, Filter, MatchValue, PointStruct

from app.clients.qdrant_store import PROFILE_SNAPSHOTS, USER_PROFILES

_ADVANCED_TERMS = [
    "algorithm", "complexity", "async", "concurrency", "mutex", "semaphore",
    "polymorphism", "recursion", "dynamic programming", "big o", "linear algebra",
    "gradient descent", "backpropagation", "distributed", "microservices",
    "kubernetes", "terraform", "eigenvector", "fourier", "cryptograph",
    "deadlock", "race condition", "memory leak", "heap allocation",
]
_INTERMEDIATE_TERMS = [
    "function", "class", "struct", "implement", "interface", "database",
    "api", "framework", "library", "debug", "refactor", "performance",
    "cache", "index", "query", "deploy", "container", "docker",
]
_WORKING_ON_PHRASES = [
    "i'm working on ", "i am working on ", "we are working on ", "we're working on "
]


@dataclass
class UserProfile:
    level: str = "unknown"
    style: str = "neutral"
    preferred_lang: str = "auto"
    topics: list[str] = field(default_factory=list)
    project_context: str | None = None
    interaction_count: int = 0


@dataclass
class ProfileSignal:
    kind: str  # advanced_question | expert_question | project_context | language_preference | correction
    value: str | None = None


def get_or_create_profile(client: QdrantClient, user_id: uuid.UUID) -> UserProfile:
    points = client.retrieve(collection_name=USER_PROFILES, ids=[str(user_id)])
    if points:
        p = points[0].payload
        return UserProfile(
            level=p["level"],
            style=p["style"],
            preferred_lang=p["preferred_lang"],
            topics=p["topics"],
            project_context=p["project_context"],
            interaction_count=p["interaction_count"],
        )

    default = UserProfile()
    client.upsert(
        collection_name=USER_PROFILES,
        points=[
            PointStruct(
                id=str(user_id),
                vector=[0.0],
                payload={
                    "level": default.level,
                    "style": default.style,
                    "preferred_lang": default.preferred_lang,
                    "topics": default.topics,
                    "project_context": default.project_context,
                    "interaction_count": default.interaction_count,
                    "updated_at": datetime.datetime.utcnow().isoformat(),
                },
            )
        ],
        wait=True,
    )
    return default


def upsert_profile(client: QdrantClient, user_id: uuid.UUID, profile: UserProfile) -> None:
    client.upsert(
        collection_name=USER_PROFILES,
        points=[
            PointStruct(
                id=str(user_id),
                vector=[0.0],
                payload={
                    "level": profile.level,
                    "style": profile.style,
                    "preferred_lang": profile.preferred_lang,
                    "topics": profile.topics,
                    "project_context": profile.project_context,
                    "interaction_count": profile.interaction_count,
                    "updated_at": datetime.datetime.utcnow().isoformat(),
                },
            )
        ],
        wait=True,
    )
    maybe_snapshot_profile(client, user_id, profile)


def detect_level_from_question(content: str) -> str | None:
    if len(content) < 50:
        return None

    lower = content.lower()

    advanced_count = sum(1 for t in _ADVANCED_TERMS if t in lower)
    if advanced_count >= 2 or (len(content) > 200 and advanced_count >= 1):
        return "expert"

    intermediate_count = sum(1 for t in _INTERMEDIATE_TERMS if t in lower)
    if len(content) > 200 and intermediate_count >= 2:
        return "intermediate"

    return None


def update_profile_from_signals(client: QdrantClient, user_id: uuid.UUID, signal: ProfileSignal) -> None:
    profile = get_or_create_profile(client, user_id)

    if signal.kind == "advanced_question":
        if profile.level in ("unknown", "beginner"):
            profile.level = "intermediate"
        elif profile.level == "intermediate":
            profile.level = "advanced"
    elif signal.kind == "expert_question":
        profile.level = "advanced"
    elif signal.kind == "project_context":
        profile.project_context = signal.value
    elif signal.kind == "language_preference":
        profile.preferred_lang = signal.value
    elif signal.kind == "correction":
        profile.interaction_count += 1

    upsert_profile(client, user_id, profile)


def update_profile_from_message(client: QdrantClient, user_id: uuid.UUID, user_message: str) -> None:
    level = detect_level_from_question(user_message)
    if level == "expert":
        update_profile_from_signals(client, user_id, ProfileSignal(kind="expert_question"))
    elif level == "intermediate":
        update_profile_from_signals(client, user_id, ProfileSignal(kind="advanced_question"))

    lower = user_message.lower()
    for phrase in _WORKING_ON_PHRASES:
        idx = lower.find(phrase)
        if idx != -1:
            after = user_message[idx + len(phrase) :]
            ctx = after.split(".")[0].strip()
            if ctx and len(ctx) < 200:
                update_profile_from_signals(client, user_id, ProfileSignal(kind="project_context", value=ctx))
                break

    vietnamese_chars = sum(
        1 for c in user_message if (0x1EA0 <= ord(c) <= 0x1EFF) or c in ("Đ", "đ")
    )
    total_alpha = sum(1 for c in user_message if c.isalpha())
    if total_alpha > 20 and vietnamese_chars * 4 > total_alpha:
        update_profile_from_signals(client, user_id, ProfileSignal(kind="language_preference", value="vi"))


def current_quarter_period() -> str:
    now = datetime.datetime.utcnow()
    quarter = (now.month - 1) // 3 + 1
    return f"{now.year}-Q{quarter}"


def maybe_snapshot_profile(client: QdrantClient, user_id: uuid.UUID, profile: UserProfile) -> None:
    period = current_quarter_period()

    points, _ = client.scroll(
        collection_name=PROFILE_SNAPSHOTS,
        scroll_filter=Filter(
            must=[
                FieldCondition(key="user_id", match=MatchValue(value=str(user_id))),
                FieldCondition(key="period", match=MatchValue(value=period)),
            ]
        ),
        limit=1,
    )
    if points:
        return

    client.upsert(
        collection_name=PROFILE_SNAPSHOTS,
        points=[
            PointStruct(
                id=str(uuid.uuid4()),
                vector=[0.0],
                payload={
                    "user_id": str(user_id),
                    "period": period,
                    "snapshot": {
                        "level": profile.level,
                        "style": profile.style,
                        "preferred_lang": profile.preferred_lang,
                        "topics": profile.topics,
                        "project_context": profile.project_context,
                        "interaction_count": profile.interaction_count,
                    },
                    "created_at": datetime.datetime.utcnow().isoformat(),
                },
            )
        ],
        wait=True,
    )


# --- REST layer ---

from fastapi import APIRouter, Header
from pydantic import BaseModel

from app.dashboard.tracker import track_usage


class ProfileUpdateRequest(BaseModel):
    level: str | None = None
    style: str | None = None
    preferred_lang: str | None = None
    project_context: str | None = None


def build_profile_router(get_client, get_usage_store=None) -> APIRouter:
    router = APIRouter()

    @router.get("/api/profile")
    async def get_profile(x_user_id: str = Header(...)):
        with track_usage(get_usage_store() if get_usage_store else None, "get_profile", x_user_id):
            profile = get_or_create_profile(get_client(), uuid.UUID(x_user_id))
            return profile.__dict__

    @router.patch("/api/profile")
    async def patch_profile(req: ProfileUpdateRequest, x_user_id: str = Header(...)):
        with track_usage(get_usage_store() if get_usage_store else None, "patch_profile", x_user_id):
            client = get_client()
            user_id = uuid.UUID(x_user_id)
            profile = get_or_create_profile(client, user_id)

            if req.level is not None:
                profile.level = req.level
            if req.style is not None:
                profile.style = req.style
            if req.preferred_lang is not None:
                profile.preferred_lang = req.preferred_lang
            if req.project_context is not None:
                profile.project_context = req.project_context or None

            upsert_profile(client, user_id, profile)
            return profile.__dict__

    return router
