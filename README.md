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
```
