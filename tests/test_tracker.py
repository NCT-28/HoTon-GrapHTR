from datetime import datetime

import pytest

from app.dashboard.tracker import track_usage


def test_track_usage_records_success(usage_store):
    with track_usage(usage_store, "retrieve_chunks", "u1"):
        pass

    assert len(usage_store.events) == 1
    event = usage_store.events[0]
    assert event["tool_name"] == "retrieve_chunks"
    assert event["user_id"] == "u1"
    assert event["repo_id"] is None
    assert event["success"] is True
    assert event["error_message"] is None
    assert event["duration_ms"] >= 0
    assert isinstance(event["created_at"], datetime)


def test_track_usage_records_repo_id_when_given(usage_store):
    with track_usage(usage_store, "query_code_graph", "u1", repo_id="r1"):
        pass

    assert usage_store.events[0]["repo_id"] == "r1"


def test_track_usage_records_failure_and_reraises(usage_store):
    with pytest.raises(ValueError, match="boom"):
        with track_usage(usage_store, "ingest_codebase", "u1"):
            raise ValueError("boom")

    event = usage_store.events[0]
    assert event["success"] is False
    assert event["error_message"] == "boom"


def test_track_usage_truncates_long_error_messages(usage_store):
    long_message = "x" * 1000
    with pytest.raises(ValueError):
        with track_usage(usage_store, "ingest_codebase", "u1"):
            raise ValueError(long_message)

    assert len(usage_store.events[0]["error_message"]) == 500


def test_track_usage_is_a_noop_when_store_is_none():
    with track_usage(None, "retrieve_chunks", "u1"):
        pass  # must not raise
