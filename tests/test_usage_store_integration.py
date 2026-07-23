import os
from datetime import datetime, timedelta, timezone

import psycopg
import pytest

from app.dashboard.usage_store import PostgresUsageStore, bootstrap_usage_database

USAGE_DB_TEST_URL = os.environ.get("USAGE_DB_TEST_URL")

pytestmark = pytest.mark.skipif(
    not USAGE_DB_TEST_URL, reason="set USAGE_DB_TEST_URL to run against a live Postgres instance"
)


@pytest.fixture
def pg_store():
    bootstrap_usage_database(USAGE_DB_TEST_URL)
    conn = psycopg.connect(USAGE_DB_TEST_URL)
    conn.execute("DELETE FROM usage_events WHERE user_id LIKE 'int-test-%'")
    conn.commit()
    conn.close()
    yield PostgresUsageStore(USAGE_DB_TEST_URL)
    conn = psycopg.connect(USAGE_DB_TEST_URL)
    conn.execute("DELETE FROM usage_events WHERE user_id LIKE 'int-test-%'")
    conn.commit()
    conn.close()


def test_bootstrap_is_idempotent():
    bootstrap_usage_database(USAGE_DB_TEST_URL)
    bootstrap_usage_database(USAGE_DB_TEST_URL)  # must not raise


def test_record_and_counts_by_tool_round_trip_through_real_postgres(pg_store):
    pg_store.record({
        "tool_name": "int-test-tool", "user_id": "int-test-u1", "repo_id": None,
        "success": True, "error_message": None, "duration_ms": 12.5,
    })

    rows = pg_store.counts_by_tool(since=datetime.now(timezone.utc) - timedelta(hours=1))

    matching = [r for r in rows if r["tool_name"] == "int-test-tool"]
    assert matching == [{"tool_name": "int-test-tool", "calls": 1, "errors": 0, "p50_ms": 12.5}]
