# HoTon-GrapHTR

Code-aware RAG + knowledge-graph service. FastAPI app exposing REST + MCP tools for retrieval-augmented generation, code graph indexing/querying, agentic reasoning (ReAct, HyDE), and a usage dashboard.

## Features

- **RAG**: document ingestion, chunking, embedding (Sentence-Transformers) and vector search (Qdrant), plus user memory/profile stores.
- **Code graph**: stateless, one-shot `ingest_codebase` MCP tool parses a repo (tree-sitter) and writes `graph.json`/`manifest.json`/`graphtr.html` straight into that repo's own `graphtr-out/` — no server-side storage, no watcher. Query the output offline (`scripts/query.py`) or browse `graphtr.html`. Separately, RAG document ingestion extracts text entities into a Neo4j/SQLite graph (`code_graph_store`) for entity linking.
- **Agentic**: ReAct loop, HyDE, web search grading via SearXNG, routing.
- **MCP server**: tools exposed over `mcp` for agent/tool integration.
- **Dashboard**: usage tracking backed by Postgres, health/queries endpoints.

## Stack

FastAPI, Qdrant, Neo4j, Postgres, sentence-transformers, transformers/torch, tree-sitter, MCP.

## Setup

```bash
cp docker-graphtr/.env.example .env
pip install -r requirements.txt
```

Configure `.env` (see `docker-graphtr/.env.example` for all variables): Qdrant/Neo4j/Postgres connection info, embedding/reasoning model names, SearXNG/browser service URLs, dashboard credentials.

`DASHBOARD_USER` and `DASHBOARD_PASSWORD` are required for the dashboard to
serve. `install.sh` copies `docker-graphtr/.env.example` with both blank, so `/dashboard`
and `/api/dashboard/summary` return `503 dashboard auth not configured` until you
set them in `.env` and restart. This is deliberate: `install.sh` binds uvicorn to
`0.0.0.0:8030`, and the summary endpoint exposes per-user ids and counts, every
ingested repo across every user, and backend health error text.

## Run

### Docker (recommended)

```bash
docker compose -f docker-graphtr/docker-compose.yml up --build
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

Never touches the git checkout itself, or the tracked pipeline tooling in
`scripts/` (`build_viewer.py`, `query.py`) — only the files `install.sh` generates.

Or manually:

```bash
pip install -r requirements.txt
DEPLOY_MODE=local uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8030
```

No Qdrant/Neo4j/Postgres needed — vectors, text-entity linking, and usage
tracking write to this server's own `graphtr-out/` (`qdrant/`, `graph.sqlite`,
`usage.sqlite`). `server` and `local` are two independent data stores, not a
live migration path — switching `DEPLOY_MODE` does not carry data over.

Code-graph ingest (`ingest_codebase`) is stateless in both deploy modes and
writes into the *ingested repo's own* `graphtr-out/` instead — a separate
directory from this server-local one, and unaffected by `DEPLOY_MODE`.

**Verify it's running:**

```bash
curl http://localhost:8030/health
# {"status":"ok"}

ls graphtr-out/
# qdrant/  graph.sqlite  usage.sqlite
```

**Config:** set `DEPLOY_MODE=local` either as an env var (as above) or in
`.env` (`cp docker-graphtr/.env.example .env`, then edit `DEPLOY_MODE=local`).
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
  graph/      # code parser + stateless snapshot writer, text-entity extraction/linking
  rag/        # chunking, retrieval, documents, memory, profile
  config.py   # Settings (pydantic-settings, env-driven)
  main.py     # FastAPI app factory
  mcp_server.py
scripts/      # knowledge-base build/index, skill bootstrap
tests/
docker-graphtr/ # Dockerfile, docker-compose.yml, .env.example
install.sh    # zero-service installer -- clones (if needed) + sets up + runs
uninstall.sh  # removes what install.sh created (.venv/, local data, optionally .env)
```

## Using graphtr in another project

`graphtr` (code graph) and `graphtr-knowledge` (narrative docs) are skills this
repo hosts — adopting them elsewhere means copying the skill files into that
project and pointing a Claude session there at this repo's running server.

**Recommended — one command, from inside the target project:**

```bash
cd /path/to/other-project
curl -fsSL https://raw.githubusercontent.com/NCT-28/HoTon-GrapHTR/develop/install.sh | bash -s -- --run
```

Since this isn't run from inside a HoTon-GrapHTR checkout, it clones one into
`~/.graphtr`, starts the server there, then auto-bootstraps the *calling*
project (`other-project`): copies `.claude/skills/graphtr/` and
`.claude/skills/graphtr-knowledge/` in, and runs
`claude mcp add --transport http hoton-graphtr http://localhost:8030/mcp -s local`
so a Claude session in `other-project` can see the tools. Safe to re-run.

**Shared server — one hoton-graphtr instance, multiple other repos calling in:**
this is the `--run` case above minus the "runs its own server" part. One
machine runs hoton-graphtr; each consumer repo (same host or a different one)
only registers as a client:

```bash
python3 scripts/init_graphtr_skills.py /path/to/other-project   # from this repo, once per consumer
cd /path/to/other-project && claude mcp add --transport http hoton-graphtr <server-url>/mcp -s local
```

`init_graphtr_skills.py` (run from this repo) copies both skills into the
target, rewriting `graphtr`'s script paths to invoke this repo's
`scripts/query.py`/`build_viewer.py` directly (the target doesn't get its own
copy) and bundling `graphtr-knowledge`'s scripts under the target's own skill
dir (it has no hoton-graphtr checkout to point at).

`<server-url>` is wherever hoton-graphtr is reachable from the consumer repo
— `http://localhost:8030` only works if consumer and server share a host;
otherwise use the server's actual host/IP and make sure the port is reachable
(firewall, security group, etc).

**Filesystem note — this is the part that actually breaks in a multi-repo
setup:** `ingest_codebase(source=...)` resolves `source` on the *server
process's* filesystem (`app/mcp_server.py::ingest_codebase_impl` →
`resolve_repo_source`), not the machine the Claude session runs on. If the
consumer repo only exists on its own host, the server can't see it just
because you registered the MCP connection:
- **Docker deploy**: copy/rsync the repo into the server container's
  `code-repos` bind mount first, then pass the in-container path
  (`/data/code-repos/<repo>`) as `source` — see the `graphtr` skill.
- **Bare/zero-service server on a separate host**: same problem, no built-in
  mount — rsync/scp the repo to that host (or put both on a shared/NFS
  volume) and pass that host-side path. There's no upload-over-MCP path; git
  URLs are explicitly rejected by `ingest_codebase` too (`mcp_server.py:176`).
- **Server and consumer repo on the same host** (just not run via `--run`
  from inside it): no issue, pass the path as-is.

Once `source` is reachable, first use in a Claude session on the target
project is the `graphtr` skill's Bootstrap step:
`ingest_codebase(source="<path as seen by the server>")` — writes
`graphtr-out/` into the *server-visible* copy of the repo, so on a
remote/Docker server that's the synced copy, not your working tree. Query
results (`graphtr-out/graph.json`) then need to be pulled back if you want
them in your own checkout too.
