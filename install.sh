#!/usr/bin/env bash
# Single-file zero-service (DEPLOY_MODE=local) installer.
#
# Brand-new machine, nothing cloned yet (public repo, plain HTTPS, no auth):
#   curl -fsSL https://raw.githubusercontent.com/NCT-28/HoTon-GrapHTR/main/install.sh | bash
#
# Already have the repo cloned -- run from the repo root:
#   bash install.sh --run
#
# Custom target dir when piping (note the `-s --` to pass args through a piped script):
#   curl -fsSL .../install.sh | bash -s -- my-dir --run
#
# Sets up a venv, installs requirements.txt, writes DEPLOY_MODE=local into
# .env, and (with --run) starts the server. No Docker, no Qdrant/Neo4j/Postgres.
set -euo pipefail

REPO_URL="${HOTON_GRAPHTR_REPO_URL:-https://github.com/NCT-28/HoTon-GrapHTR.git}"

RUN_AFTER=0
TARGET_DIR=""
for arg in "$@"; do
  case "$arg" in
    --run) RUN_AFTER=1 ;;
    *) TARGET_DIR="$arg" ;;
  esac
done

# Already inside a checkout of this repo? Operate in place -- no clone needed.
# Otherwise (e.g. curl-piped on a fresh machine) clone/pull into TARGET_DIR.
if [ -f "requirements.txt" ] && [ -f "app/main.py" ]; then
  REPO_ROOT="$(pwd)"
else
  TARGET_DIR="${TARGET_DIR:-HoTon-GrapHTR}"

  if ! command -v git >/dev/null 2>&1; then
    echo "Error: git is required." >&2
    exit 1
  fi

  if [ -d "$TARGET_DIR/.git" ]; then
    echo "Found existing checkout at $TARGET_DIR, pulling latest..."
    git -C "$TARGET_DIR" pull --ff-only
  else
    echo "Cloning $REPO_URL into $TARGET_DIR..."
    git clone "$REPO_URL" "$TARGET_DIR"
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

if [ "$RUN_AFTER" -eq 1 ]; then
  echo ""
  echo "Starting server on :8030..."
  exec uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8030
else
  echo ""
  echo "Run:"
  echo "  cd $REPO_ROOT && source .venv/bin/activate"
  echo "  uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8030"
  echo ""
  echo "Verify:"
  echo "  curl http://localhost:8030/health"
fi
