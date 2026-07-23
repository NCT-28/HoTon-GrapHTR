# HoTon-GrapHTR

Code-aware RAG + knowledge-graph service. FastAPI app exposing REST + MCP tools for retrieval-augmented generation, code graph indexing/querying, agentic reasoning (ReAct, HyDE), and a usage dashboard.

## Features

- **RAG**: document ingestion, chunking, embedding (Sentence-Transformers) and vector search (Qdrant), plus user memory/profile stores.
- **Code graph**: parses repos (tree-sitter) into a graph (Neo4j via `code_graph_store`), with entity extraction/linking, repo watching for live reindex, and graph query endpoints.
- **Agentic**: ReAct loop, HyDE, web search grading via SearXNG, routing.
- **MCP server**: tools exposed over `mcp` for agent/tool integration.
- **Dashboard**: usage tracking backed by Postgres, health/queries endpoints.

## Stack

FastAPI, Qdrant, Neo4j, Postgres, sentence-transformers, transformers/torch, tree-sitter, MCP.

## Setup

```bash
cp docker/.env.example .env
pip install -r requirements.txt
```

Configure `.env` (see `docker/.env.example` for all variables): Qdrant/Neo4j/Postgres connection info, embedding/reasoning model names, SearXNG/browser service URLs, dashboard credentials.

## Run

### Docker (recommended)

```bash
docker compose -f docker/docker-compose.yml up --build
```

Starts the app plus Qdrant, Neo4j, and Postgres. App listens on `:8030`.

### Local

Requires Qdrant/Neo4j/Postgres running and reachable per `.env`.

```bash
uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8030
```

### Zero-service (no Docker, no external DB)

Fastest path on a brand-new machine (nothing pre-cloned, public repo, plain HTTPS):

```bash
curl -fsSL https://raw.githubusercontent.com/NCT-28/HoTon-GrapHTR/main/install.sh | bash
```

Clones into `./HoTon-GrapHTR` and runs the setup below automatically. Custom
target dir: `curl -fsSL .../install.sh | bash -s -- my-dir` (note the `-s --`
needed to pass args through a piped script).

If you already have the repo cloned:

```bash
bash scripts/setup_zero_service.sh --run   # creates .venv, installs deps, sets DEPLOY_MODE=local, starts the server
```

`scripts/setup_zero_service.sh` (no `--run`) does the same setup without
starting the server — creates `.venv`, installs `requirements.txt`, copies
`docker/.env.example` to `.env` if missing, and sets `DEPLOY_MODE=local` in
it. Safe to re-run.

Or manually:

```bash
pip install -r requirements.txt
DEPLOY_MODE=local uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8030
```

No Qdrant/Neo4j/Postgres needed — vectors, the code graph, and usage tracking
all write to `graphtr-out/` (`qdrant/`, `graph.sqlite`, `usage.sqlite`).
`server` and `local` are two independent data stores, not a live migration
path — switching `DEPLOY_MODE` does not carry data over.

**Verify it's running:**

```bash
curl http://localhost:8030/health
# {"status":"ok"}

ls graphtr-out/
# qdrant/  graph.sqlite  usage.sqlite
```

**Config:** set `DEPLOY_MODE=local` either as an env var (as above) or in
`.env` (`cp docker/.env.example .env`, then edit `DEPLOY_MODE=local`).
`LOCAL_DATA_DIR` (default `./graphtr-out`) controls where the three files
land — set it to point elsewhere if you don't want them under the repo.

**Switching back to `server` mode:** unset `DEPLOY_MODE` (or set it back to
`server`) and restart — it reconnects to Qdrant/Neo4j/Postgres per `.env`.
The `graphtr-out/` files from local mode are untouched and unused; delete
them manually if you want to reclaim the disk space.

## Tests

```bash
pytest
```

## Project layout

```
app/
  agentic/    # ReAct, HyDE, grading, routing
  clients/    # embeddings, llm, qdrant, browser clients
  dashboard/  # usage tracking, health, queries, router
  graph/      # code graph store, parser, entity extraction/linking, repo watcher
  rag/        # chunking, retrieval, documents, memory, profile
  config.py   # Settings (pydantic-settings, env-driven)
  main.py     # FastAPI app factory
  mcp_server.py
scripts/      # knowledge-base build/index, skill bootstrap, setup_zero_service.sh
tests/
docker/       # Dockerfile, docker-compose.yml, .env.example
install.sh    # curl-pipeable installer for a brand-new machine
```
