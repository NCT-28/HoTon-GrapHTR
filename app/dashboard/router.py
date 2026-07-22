"""GET /dashboard (HTML page) and GET /api/dashboard/summary (JSON), both
behind HTTP Basic Auth. Fails closed: if DASHBOARD_USER/DASHBOARD_PASSWORD are
unset, both routes return 503 rather than serving unauthenticated."""

import secrets
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.config import get_settings
from app.dashboard import health, queries

_TEMPLATE_PATH = Path(__file__).parent / "templates" / "dashboard.html"
_security = HTTPBasic()


def _require_auth(credentials: HTTPBasicCredentials = Depends(_security)) -> None:
    settings = get_settings()
    if not settings.dashboard_user or not settings.dashboard_password:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="dashboard auth not configured")
    user_ok = secrets.compare_digest(credentials.username, settings.dashboard_user)
    pass_ok = secrets.compare_digest(credentials.password, settings.dashboard_password)
    if not (user_ok and pass_ok):
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
