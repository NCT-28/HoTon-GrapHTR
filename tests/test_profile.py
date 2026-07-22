import uuid

from app.rag.profile import (
    ProfileSignal,
    UserProfile,
    current_quarter_period,
    detect_level_from_question,
    get_or_create_profile,
    update_profile_from_message,
    update_profile_from_signals,
    upsert_profile,
)
from app.clients.qdrant_store import PROFILE_SNAPSHOTS


def test_get_or_create_profile_creates_default(qdrant):
    user_id = uuid.uuid4()
    profile = get_or_create_profile(qdrant, user_id)
    assert profile == UserProfile()


def test_upsert_then_get_returns_saved_values(qdrant):
    user_id = uuid.uuid4()
    profile = UserProfile(level="advanced", style="terse", preferred_lang="vi")
    upsert_profile(qdrant, user_id, profile)

    fetched = get_or_create_profile(qdrant, user_id)
    assert fetched.level == "advanced"
    assert fetched.style == "terse"
    assert fetched.preferred_lang == "vi"


def test_detect_level_short_message_returns_none():
    assert detect_level_from_question("hi") is None


def test_detect_level_advanced_terms():
    msg = "Can you explain how gradient descent and backpropagation interact in distributed training with mutex locks and race conditions?"
    assert detect_level_from_question(msg) == "expert"


def test_detect_level_intermediate_terms():
    # Needs len(content) > 200 per the ported threshold (matches profile.rs's
    # `content.len() > 200 && intermediate_count >= 2`) — a shorter message with the
    # same terms stays under the length gate and returns None instead.
    msg = (
        "I need to implement a function that queries the database through an API and "
        "uses a cache for performance, then deploy it in a docker container. This is "
        "part of a larger refactor effort for our backend service overall design plan."
    )
    assert detect_level_from_question(msg) == "intermediate"


def test_update_profile_from_signals_advanced_question(qdrant):
    user_id = uuid.uuid4()
    update_profile_from_signals(qdrant, user_id, ProfileSignal(kind="advanced_question"))
    profile = get_or_create_profile(qdrant, user_id)
    assert profile.level == "intermediate"


def test_update_profile_from_message_detects_vietnamese(qdrant):
    user_id = uuid.uuid4()
    # The ported detector only counts the narrow U+1EA0-1EFF codepoint block (+ Đ/đ) —
    # per profile.rs's comment, this is deliberately narrow to avoid false positives from
    # French/German/Romanian etc. Ordinary Vietnamese prose mixes in plain Latin-1 accented
    # letters (à, á, ô, ê...) that fall outside this range, so natural sentences rarely
    # clear the >25%-of-all-alphabetic-chars threshold. This message is dense in qualifying
    # tone-marked vowels specifically to verify the detector's arithmetic, not to read as
    # natural prose.
    msg = "ệ ố ữ ạ ậ ử ộ ấ ẽ ề ả ẫ ẻ ẹ ễ ỉ ị ọ ỏ ợ ụ ủ ứ ừ ỳ"
    update_profile_from_message(qdrant, user_id, msg)
    profile = get_or_create_profile(qdrant, user_id)
    assert profile.preferred_lang == "vi"


def test_update_profile_from_message_detects_project_context(qdrant):
    user_id = uuid.uuid4()
    msg = "I'm working on a chatbot for customer support. Can you help me design the API?"
    update_profile_from_message(qdrant, user_id, msg)
    profile = get_or_create_profile(qdrant, user_id)
    assert profile.project_context == "a chatbot for customer support"


def test_current_quarter_period_format():
    period = current_quarter_period()
    year, q = period.split("-")
    assert year.isdigit()
    assert q.startswith("Q")
    assert 1 <= int(q[1:]) <= 4


def test_upsert_profile_snapshots_once_per_quarter(qdrant):
    user_id = uuid.uuid4()
    upsert_profile(qdrant, user_id, UserProfile(level="advanced"))
    upsert_profile(qdrant, user_id, UserProfile(level="advanced", interaction_count=1))

    points, _ = qdrant.scroll(collection_name=PROFILE_SNAPSHOTS, limit=10)
    matching = [p for p in points if p.payload["user_id"] == str(user_id)]
    assert len(matching) == 1
