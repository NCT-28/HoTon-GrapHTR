#!/usr/bin/env bash
# Single-file zero-service (DEPLOY_MODE=local) installer.
#
# Brand-new machine, nothing cloned yet (public repo, plain HTTPS, no auth):
#   curl -fsSL https://raw.githubusercontent.com/NCT-28/HoTon-GrapHTR/develop/install.sh | bash
#
# Already have the repo cloned -- run from the repo root:
#   bash install.sh --run
#
# Clones into ~/.graphtr by default -- a fixed location so running the
# curl one-liner from inside some other project doesn't drop a checkout into
# that project's directory. Custom target dir (note the `-s --` to pass args
# through a piped script):
#   curl -fsSL .../install.sh | bash -s -- my-dir --run
#
# Sets up a venv, installs requirements.txt, writes DEPLOY_MODE=local into
# .env, and (with --run) starts the server. No Docker, no Qdrant/Neo4j/Postgres.
# Clones the develop branch by default (override with HOTON_GRAPHTR_REPO_BRANCH).
set -euo pipefail

# Captured before any `cd` -- the directory the script was invoked from, i.e.
# the consumer project (e.g. curl-piped from inside some other repo). Used
# below to auto-bootstrap that project's graphtr skills + MCP registration.
ORIGINAL_PWD="$(pwd)"

REPO_URL="${HOTON_GRAPHTR_REPO_URL:-https://github.com/NCT-28/HoTon-GrapHTR.git}"
# Zero-service (DEPLOY_MODE=local) code lives on develop and hasn't been
# merged to main yet -- cloning the default branch gets a Settings class
# without the deploy_mode field, so DEPLOY_MODE=local fails with
# pydantic's extra_forbidden. Pin to develop until that merge happens.
REPO_BRANCH="${HOTON_GRAPHTR_REPO_BRANCH:-develop}"

RUN_AFTER=0
TARGET_DIR=""
for arg in "$@"; do
  case "$arg" in
    --run) RUN_AFTER=1 ;;
    *) TARGET_DIR="$arg" ;;
  esac
done

# Already inside a checkout of this repo? Operate in place -- no clone needed.
# app/mcp_server.py is an unusual enough filename to avoid false-positiving
# on some other project that happens to also have requirements.txt/app/main.py.
# Otherwise (e.g. curl-piped on a fresh machine) clone/pull into TARGET_DIR.
if [ -f "requirements.txt" ] && [ -f "app/main.py" ] && [ -f "app/mcp_server.py" ]; then
  REPO_ROOT="$(pwd)"
else
  if [ -z "$TARGET_DIR" ]; then
    if [ -z "${HOME:-}" ]; then
      echo "Error: \$HOME is not set, cannot determine default install location (~/.graphtr)." >&2
      echo "Pass a target dir explicitly instead: bash install.sh /path/to/dir" >&2
      exit 1
    fi
    TARGET_DIR="$HOME/.graphtr"
  fi

  if ! command -v git >/dev/null 2>&1; then
    echo "Error: git is required." >&2
    exit 1
  fi

  if [ -d "$TARGET_DIR/.git" ]; then
    echo "Found existing checkout at $TARGET_DIR, pulling latest..."
    git -C "$TARGET_DIR" checkout "$REPO_BRANCH"
    git -C "$TARGET_DIR" pull --ff-only
  else
    echo "Cloning $REPO_URL ($REPO_BRANCH) into $TARGET_DIR..."
    git clone -b "$REPO_BRANCH" "$REPO_URL" "$TARGET_DIR"
  fi

  REPO_ROOT="$(cd "$TARGET_DIR" && pwd)"
fi

cd "$REPO_ROOT"

# app/graph/code_graph_store.py and others use `X | None` type hints evaluated
# at import time (PEP 604) -- requires Python 3.10+. Plain `python3` is too
# old on some systems (e.g. macOS system Python is 3.9), so probe newer
# interpreters first.
PYTHON_BIN=""
for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$candidate" >/dev/null 2>&1; then
    version=$("$candidate" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')
    major=${version%%.*}
    minor=${version##*.}
    if [ "$major" -eq 3 ] && [ "$minor" -ge 10 ]; then
      PYTHON_BIN="$candidate"
      break
    fi
  fi
done

if [ -z "$PYTHON_BIN" ]; then
  echo "Error: need Python 3.10+ (checked python3.13/3.12/3.11/3.10/python3), none found." >&2
  exit 1
fi
echo "Using $PYTHON_BIN ($("$PYTHON_BIN" --version))"

VENV_DIR="$REPO_ROOT/.venv"
if [ ! -d "$VENV_DIR" ]; then
  echo "Creating virtualenv at $VENV_DIR"
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

echo "Installing dependencies (torch/transformers make first run slow, be patient)..."
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt

ENV_FILE="$REPO_ROOT/.env"
if [ ! -f "$ENV_FILE" ]; then
  echo "Creating .env from docker/.env.example"
  cp "$REPO_ROOT/docker/.env.example" "$ENV_FILE"
fi

if grep -q '^DEPLOY_MODE=' "$ENV_FILE"; then
  # -i.bak works on both BSD sed (macOS) and GNU sed (Linux); plain -i does not.
  sed -i.bak 's/^DEPLOY_MODE=.*/DEPLOY_MODE=local/' "$ENV_FILE"
  rm -f "$ENV_FILE.bak"
else
  echo "DEPLOY_MODE=local" >>"$ENV_FILE"
fi

echo ""
echo "Setup done. .env has DEPLOY_MODE=local."
echo "Data will be written under \$LOCAL_DATA_DIR (default ./graphtr-out)."

# Auto-bootstrap the calling project: copy the graphtr/graphtr-knowledge
# skills into it and register the hoton-graphtr MCP server, so `graphtr-out/`
# can actually be built there. Skipped when running in-place inside this repo
# itself (ORIGINAL_PWD == REPO_ROOT) since there's no separate consumer
# project to wire up. Best-effort: failures here must not fail server setup.
if [ "$ORIGINAL_PWD" != "$REPO_ROOT" ]; then
  if [ ! -f "$ORIGINAL_PWD/.claude/skills/graphtr/SKILL.md" ]; then
    echo ""
    echo "Setting up graphtr skills in $ORIGINAL_PWD..."
    "$PYTHON_BIN" "$REPO_ROOT/scripts/init_graphtr_skills.py" "$ORIGINAL_PWD" || \
      echo "Warning: skill setup in $ORIGINAL_PWD failed, continuing." >&2
  fi

  # If graphtr-out/graph.json already exists (project was ingested before,
  # e.g. re-running install.sh), (re)build the viewer right away instead of
  # leaving graphtr.html stale. Fresh projects have no graph.json yet -- that
  # only shows up after the Bootstrap ingest/export/write steps run inside a
  # Claude session -- so this is a no-op there.
  GRAPHTR_OUT_DIR="$ORIGINAL_PWD/graphtr-out"
  if [ -f "$GRAPHTR_OUT_DIR/graph.json" ]; then
    echo "Building graphtr viewer for $ORIGINAL_PWD..."
    "$PYTHON_BIN" "$REPO_ROOT/graphtr-out/build_viewer.py" --out-dir "$GRAPHTR_OUT_DIR" || \
      echo "Warning: build_viewer.py failed for $GRAPHTR_OUT_DIR" >&2
  fi

  if command -v claude >/dev/null 2>&1; then
    if ! (cd "$ORIGINAL_PWD" && claude mcp list 2>/dev/null | grep -q "hoton-graphtr"); then
      echo "Registering hoton-graphtr MCP server in $ORIGINAL_PWD..."
      (cd "$ORIGINAL_PWD" && claude mcp add --transport http hoton-graphtr http://localhost:8030/mcp -s local) || \
        echo "Warning: MCP registration in $ORIGINAL_PWD failed, add manually:" \
             "claude mcp add --transport http hoton-graphtr http://localhost:8030/mcp -s local" >&2
    fi
  else
    echo "Note: 'claude' CLI not found -- register the MCP server manually:"
    echo "  cd $ORIGINAL_PWD && claude mcp add --transport http hoton-graphtr http://localhost:8030/mcp -s local"
  fi
fi

if [ "$RUN_AFTER" -eq 1 ]; then
  # A leftover server from a previous run holds the local Qdrant storage
  # lock (./graphtr-out/qdrant), so a fresh start fails with
  # portalocker.AlreadyLocked. Stop anything already bound to :8030 first.
  if command -v lsof >/dev/null 2>&1; then
    EXISTING_PIDS=$(lsof -ti tcp:8030 2>/dev/null || true)
    if [ -n "$EXISTING_PIDS" ]; then
      echo ""
      echo "Stopping existing process(es) on :8030: $EXISTING_PIDS"
      kill $EXISTING_PIDS 2>/dev/null || true
      sleep 1
      STILL_RUNNING=$(lsof -ti tcp:8030 2>/dev/null || true)
      if [ -n "$STILL_RUNNING" ]; then
        kill -9 $STILL_RUNNING 2>/dev/null || true
      fi
    fi
  fi

  LOG_FILE="$REPO_ROOT/graphtr-server.log"
  echo ""
  echo "Starting server on :8030 (detached -- survives Ctrl+C / shell exit)..."
  nohup uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8030 \
    >"$LOG_FILE" 2>&1 </dev/null &
  SERVER_PID=$!
  disown
  echo "Server started (pid $SERVER_PID). Logs: $LOG_FILE"
  echo "Stop with: kill $SERVER_PID"
else
  echo ""
  echo "Run:"
  echo "  cd $REPO_ROOT && source .venv/bin/activate"
  echo "  uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8030"
  echo ""
  echo "Verify:"
  echo "  curl http://localhost:8030/health"
fi
