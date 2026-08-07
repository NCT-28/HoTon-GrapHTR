"""GET /dashboard (HTML page) and GET /api/dashboard/summary (JSON), both
behind HTTP Basic Auth. Fails CLOSED: if DASHBOARD_USER/DASHBOARD_PASSWORD are
unset, both routes return 503 rather than serving unauthenticated. Both
credentials default to empty and docker/.env.example ships them blank, so
failing open would expose usage stats, health (including backend error text),
every repo across every user, and the per-user breakdown on any default
install -- install.sh binds uvicorn to 0.0.0.0."""

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
        # Fail CLOSED. Both settings default to empty and docker/.env.example ships
        # them blank, so failing open meant a default install served usage stats,
        # per-user breakdowns, every repo across every user, and backend error text
        # to anything that could reach the port.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="dashboard auth not configured: set DASHBOARD_USER and DASHBOARD_PASSWORD",
        )
    if credentials is None or not (
        secrets.compare_digest(credentials.username, settings.dashboard_user)
        and secrets.compare_digest(credentials.password, dashboard_password)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )


def build_dashboard_router(get_client, get_usage_store, get_embedder) -> APIRouter:
    router = APIRouter()

    @router.get("/dashboard", response_class=HTMLResponse, dependencies=[Depends(_require_auth)])
    async def dashboard_page():
        return _TEMPLATE_PATH.read_text()

    @router.get("/api/dashboard/summary", dependencies=[Depends(_require_auth)])
    async def dashboard_summary():
        client = get_client()
        usage_store = get_usage_store()
        embedder = get_embedder()
        return {
            "health": [
                health.check_qdrant(client),
                health.check_postgres(usage_store),
                health.check_embedder(embedder),
            ],
            "storage": queries.storage_breakdown(client),
            "mcp_tool_usage": queries.mcp_tool_usage(usage_store),
            "route_usage": queries.route_usage(usage_store),
            "by_user": queries.user_breakdown(client, usage_store),
        }

    return router
