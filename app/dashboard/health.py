"""Live health pings for hoton-rag's four dependencies, used by
GET /api/dashboard/summary. Each check catches its own errors — one dependency
being down must not break the others or the endpoint as a whole."""

import time


def check_qdrant(client) -> dict:
    start = time.monotonic()
    try:
        client.get_collections()
        return {"name": "qdrant", "up": True, "latency_ms": round((time.monotonic() - start) * 1000, 1)}
    except Exception as e:
        return {"name": "qdrant", "up": False, "error": str(e)[:200]}


def check_neo4j(graph_store) -> dict:
    start = time.monotonic()
    try:
        graph_store.ping()
        return {"name": "neo4j", "up": True, "latency_ms": round((time.monotonic() - start) * 1000, 1)}
    except Exception as e:
        return {"name": "neo4j", "up": False, "error": str(e)[:200]}


def check_postgres(usage_store) -> dict:
    if usage_store is None:
        return {"name": "postgres", "up": False, "error": "usage tracking disabled (USAGE_DB_URL unset)"}
    start = time.monotonic()
    try:
        usage_store.ping()
        return {"name": "postgres", "up": True, "latency_ms": round((time.monotonic() - start) * 1000, 1)}
    except Exception as e:
        return {"name": "postgres", "up": False, "error": str(e)[:200]}


def check_embedder(embedder) -> dict:
    if embedder is not None and hasattr(embedder, "embed_single"):
        return {"name": "embed_model", "up": True}
    return {"name": "embed_model", "up": False, "error": "embedder not loaded"}
