"""GET /dashboard (HTML page) and GET /api/dashboard/summary (JSON), both
behind HTTP Basic Auth. Fails OPEN: if DASHBOARD_USER/DASHBOARD_PASSWORD are
unset, both routes serve unauthenticated rather than returning 503. Only set
one of the two blank if you're intentionally exposing this on a trusted
network -- both routes return usage stats, health, and per-user breakdown."""

import secrets
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.config import get_settings
from app.dashboard import health, queries

_TEMPLATE_PATH = Path(__file__).parent / "templates" / "dashboard.html"
_security = HTTPBasic(auto_error=False)


def _require_auth(credentials: HTTPBasicCredentials | None = Depends(_security)) -> None:
    settings = get_settings()
    dashboard_password = settings.dashboard_password.get_secret_value()
    if not settings.dashboard_user or not dashboard_password:
        return
    if credentials is None or not (
        secrets.compare_digest(credentials.username, settings.dashboard_user)
        and secrets.compare_digest(credentials.password, dashboard_password)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )


def build_dashboard_router(get_client, get_graph_store, get_usage_store, get_embedder) -> APIRouter:
    router = APIRouter()

    @router.get("/dashboard", response_class=HTMLResponse, dependencies=[Depends(_require_auth)])
    async def dashboard_page():
        return _TEMPLATE_PATH.read_text()

    @router.get("/api/dashboard/summary", dependencies=[Depends(_require_auth)])
    async def dashboard_summary():
        client = get_client()
        graph_store = get_graph_store()
        usage_store = get_usage_store()
        embedder = get_embedder()
        return {
            "health": [
                health.check_qdrant(client),
                health.check_neo4j(graph_store),
                health.check_postgres(usage_store),
                health.check_embedder(embedder),
            ],
            "storage": queries.storage_breakdown(client),
            "tool_usage": queries.tool_usage(usage_store),
            "by_project": queries.project_breakdown(graph_store),
            "by_user": queries.user_breakdown(client, usage_store),
        }

    return router
