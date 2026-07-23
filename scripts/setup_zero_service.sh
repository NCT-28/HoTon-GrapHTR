#!/usr/bin/env bash
# Quick zero-service setup (DEPLOY_MODE=local) -- no Docker, no Qdrant/Neo4j/Postgres.
#
# On a new machine:
#   git clone git@github.com:NCT-28/HoTon-GrapHTR.git
#   cd HoTon-GrapHTR
#   bash scripts/setup_zero_service.sh          # setup only
#   bash scripts/setup_zero_service.sh --run    # setup, then start the server
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

RUN_AFTER=0
if [ "${1:-}" = "--run" ]; then
  RUN_AFTER=1
fi

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

if [ "$RUN_AFTER" -eq 1 ]; then
  echo ""
  echo "Starting server on :8030..."
  exec uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8030
else
  echo ""
  echo "Run:"
  echo "  source .venv/bin/activate"
  echo "  uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8030"
  echo ""
  echo "Verify:"
  echo "  curl http://localhost:8030/health"
fi
