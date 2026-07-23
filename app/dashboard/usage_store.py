"""Usage-event storage for the /dashboard monitoring page. Every MCP tool call
and HTTP route hit gets one row here (metadata only — never query/message
content). `FakeUsageStore` (an in-memory test double implementing the same
interface) lives in tests/conftest.py so every consumer can be unit-tested
without a live Postgres instance, mirroring GraphStore/FakeGraphStore."""

import os
import sqlite3
import threading
from abc import ABC, abstractmethod
from datetime import datetime
from functools import lru_cache
from urllib.parse import urlparse, urlunparse

import psycopg

from app.config import get_settings


class UsageStore(ABC):
    @abstractmethod
    def record(self, event: dict) -> None: ...

    @abstractmethod
    def counts_by_tool(self, since: datetime) -> list[dict]: ...

    @abstractmethod
    def counts_by_user(self, since: datetime) -> list[dict]: ...

    @abstractmethod
    def ping(self) -> bool: ...


def build_usage_db_url(host: str, port: int, user: str, password: str, name: str) -> str:
    """Assemble a Postgres URI with user/password percent-encoded — Docker
    Compose's ${VAR} substitution can't URL-encode, so a raw password
    containing '@', ':', '/' etc. (a real risk — this repo's own POSTGRES_PASS
    does) breaks any URI built by direct YAML interpolation. Assembling and
    encoding here, in Python, avoids that."""
    from urllib.parse import quote
    return f"postgresql://{quote(user, safe='')}:{quote(password, safe='')}@{host}:{port}/{name}"


def _admin_dsn(usage_db_url: str) -> str:
    """Same server, 'postgres' maintenance database — used only to CREATE DATABASE."""
    parsed = urlparse(usage_db_url)
    return urlunparse(parsed._replace(path="/postgres"))


def bootstrap_usage_database(usage_db_url: str) -> None:
    """Create the target database (if missing) and its one table. Safe to call
    repeatedly — mirrors app/clients/qdrant_store.py's bootstrap_collections."""
    admin_conn = psycopg.connect(_admin_dsn(usage_db_url), autocommit=True)
    try:
        db_name = urlparse(usage_db_url).path.lstrip("/")
        # db_name comes from trusted server config (env var), never user input —
        # same trust level as the Cypher edge-type interpolation in
        # app/graph/code_graph_store.py, not a SQL-injection surface.
        try:
            admin_conn.execute(f"CREATE DATABASE {db_name}")
        except psycopg.errors.DuplicateDatabase:
            pass
    finally:
        admin_conn.close()

    conn = psycopg.connect(usage_db_url)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS usage_events (
                id BIGSERIAL PRIMARY KEY,
                tool_name TEXT NOT NULL,
                user_id TEXT NOT NULL,
                repo_id TEXT,
                success BOOLEAN NOT NULL,
                error_message TEXT,
                duration_ms DOUBLE PRECISION NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS usage_events_tool_time_idx ON usage_events (tool_name, created_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS usage_events_user_time_idx ON usage_events (user_id, created_at)"
        )
        conn.commit()
    finally:
        conn.close()


class PostgresUsageStore(UsageStore):
    def __init__(self, url: str):
        self._url = url
        self._lock = threading.Lock()
        self._conn = psycopg.connect(url, autocommit=False)

    def _reconnect(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass
        self._conn = psycopg.connect(self._url, autocommit=False)

    def _with_retry(self, fn):
        # A dead connection (Postgres restart, idle timeout, network blip)
        # would otherwise stay broken until process restart, since this store
        # is cached as a process-lifetime singleton by get_usage_store().
        # Reconnect once and retry before giving up.
        with self._lock:
            try:
                return fn()
            except psycopg.OperationalError:
                self._reconnect()
                return fn()

    def record(self, event: dict) -> None:
        # event may include a "created_at" key (track_usage always sets one) —
        # unused here since the column defaults to now(); psycopg only binds
        # the %(...)s names the query text references, so the extra key is fine.
        def _do():
            self._conn.execute(
                """
                INSERT INTO usage_events (tool_name, user_id, repo_id, success, error_message, duration_ms)
                VALUES (%(tool_name)s, %(user_id)s, %(repo_id)s, %(success)s, %(error_message)s, %(duration_ms)s)
                """,
                event,
            )
            self._conn.commit()

        self._with_retry(_do)

    def counts_by_tool(self, since: datetime) -> list[dict]:
        def _do():
            return self._conn.execute(
                """
                SELECT tool_name,
                       count(*) AS calls,
                       count(*) FILTER (WHERE NOT success) AS errors,
                       percentile_cont(0.5) WITHIN GROUP (ORDER BY duration_ms) AS p50_ms
                FROM usage_events
                WHERE created_at >= %(since)s
                GROUP BY tool_name
                ORDER BY calls DESC
                """,
                {"since": since},
            ).fetchall()

        rows = self._with_retry(_do)
        return [
            {"tool_name": r[0], "calls": r[1], "errors": r[2], "p50_ms": round(r[3], 1) if r[3] is not None else 0.0}
            for r in rows
        ]

    def counts_by_user(self, since: datetime) -> list[dict]:
        def _do():
            return self._conn.execute(
                """
                SELECT user_id, count(*) AS calls
                FROM usage_events
                WHERE created_at >= %(since)s
                GROUP BY user_id
                ORDER BY calls DESC
                """,
                {"since": since},
            ).fetchall()

        rows = self._with_retry(_do)
        return [{"user_id": r[0], "calls": r[1]} for r in rows]

    def ping(self) -> bool:
        self._with_retry(lambda: self._conn.execute("SELECT 1"))
        return True


class SqliteUsageStore(UsageStore):
    """File-backed UsageStore for DEPLOY_MODE=local. p50 is computed in
    Python (sorted durations, middle element) since SQLite has no
    percentile_cont() — same approach FakeUsageStore already uses in
    tests/conftest.py."""

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock, self._conn:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS usage_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tool_name TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    repo_id TEXT,
                    success INTEGER NOT NULL,
                    error_message TEXT,
                    duration_ms REAL NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS usage_events_tool_time_idx ON usage_events (tool_name, created_at)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS usage_events_user_time_idx ON usage_events (user_id, created_at)"
            )

    def record(self, event: dict) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO usage_events (tool_name, user_id, repo_id, success, error_message, duration_ms, created_at)
                VALUES (:tool_name, :user_id, :repo_id, :success, :error_message, :duration_ms, :created_at)
                """,
                {**event, "success": int(event["success"]), "created_at": event["created_at"].isoformat()},
            )

    def counts_by_tool(self, since: datetime) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT tool_name, success, duration_ms FROM usage_events WHERE created_at >= ?",
                (since.isoformat(),),
            ).fetchall()

        by_tool: dict[str, dict] = {}
        for row in rows:
            entry = by_tool.setdefault(
                row["tool_name"], {"tool_name": row["tool_name"], "calls": 0, "errors": 0, "durations": []}
            )
            entry["calls"] += 1
            if not row["success"]:
                entry["errors"] += 1
            entry["durations"].append(row["duration_ms"])

        result = []
        for entry in by_tool.values():
            durations = sorted(entry.pop("durations"))
            entry["p50_ms"] = durations[len(durations) // 2] if durations else 0.0
            result.append(entry)
        result.sort(key=lambda r: r["calls"], reverse=True)
        return result

    def counts_by_user(self, since: datetime) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT user_id, count(*) AS calls FROM usage_events "
                "WHERE created_at >= ? GROUP BY user_id ORDER BY calls DESC",
                (since.isoformat(),),
            ).fetchall()
        return [{"user_id": row["user_id"], "calls": row["calls"]} for row in rows]

    def ping(self) -> bool:
        with self._lock:
            self._conn.execute("SELECT 1")
        return True


@lru_cache
def get_usage_store() -> "UsageStore | None":
    settings = get_settings()
    if settings.usage_db_host:
        url = build_usage_db_url(
            settings.usage_db_host, settings.usage_db_port,
            settings.usage_db_user, settings.usage_db_password.get_secret_value(), settings.usage_db_name,
        )
    elif settings.usage_db_url:
        url = settings.usage_db_url
    else:
        return None
    bootstrap_usage_database(url)
    return PostgresUsageStore(url)
