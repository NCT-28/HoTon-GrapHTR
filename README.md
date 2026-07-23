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

One script, `install.sh`, handles both a brand-new machine (clones the repo
first) and an existing checkout (runs in place) — same file either way.

Brand-new machine, nothing cloned yet (public repo, plain HTTPS, no auth):

```bash
curl -fsSL https://raw.githubusercontent.com/NCT-28/HoTon-GrapHTR/develop/install.sh | bash -s -- --run
```

Clones into `~/.graphtr` (a fixed location, so this doesn't drop a checkout
into whatever project directory you happened to run the curl command from),
sets up a venv, installs deps, sets `DEPLOY_MODE=local` in `.env`, and starts
the server. Custom target dir: `bash -s -- my-dir --run` (note the `-s --`
needed to pass args through a piped script). Drop `--run` to only set up
without starting.

Already have the repo cloned — run from the repo root:

```bash
bash install.sh --run
```

Safe to re-run; skips the clone since it detects it's already inside the
checkout (`requirements.txt` + `app/main.py` present in the cwd).

To remove what `install.sh` created (run from the repo root):

```bash
bash uninstall.sh              # prompts, removes .venv/ and graphtr-out/'s local data (graph.sqlite, usage.sqlite, qdrant/)
bash uninstall.sh -y           # same, no prompt
bash uninstall.sh --purge-env  # also delete .env
```

Never touches the git checkout itself, or `graphtr-out/`'s tracked tooling
(`build_viewer.py`, `query.py`) — only the files `install.sh` generates.

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
scripts/      # knowledge-base build/index, skill bootstrap
tests/
docker/       # Dockerfile, docker-compose.yml, .env.example
install.sh    # zero-service installer -- clones (if needed) + sets up + runs
uninstall.sh  # removes what install.sh created (.venv/, local data, optionally .env)
```
