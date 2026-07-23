#!/usr/bin/env bash
# Reverses what install.sh created for zero-service (DEPLOY_MODE=local) mode:
# the venv and the local Qdrant/SQLite data. Run from the repo root.
#
#   bash uninstall.sh              # prompts before deleting
#   bash uninstall.sh -y           # skip confirmation
#   bash uninstall.sh --purge-env  # also delete .env (combine with -y to skip prompt)
#
# Only ever touches files install.sh creates: .venv/, graphtr-out/graph.sqlite,
# graphtr-out/usage.sqlite, graphtr-out/qdrant/, and (with --purge-env) .env.
# graphtr-out/ itself is NOT removed -- it also holds tracked tooling
# (build_viewer.py, query.py) unrelated to zero-service mode. The git
# checkout itself is never touched.
set -euo pipefail

if [ ! -f "requirements.txt" ] || [ ! -f "app/main.py" ]; then
  echo "Error: run this from the repo root (requirements.txt / app/main.py not found here)." >&2
  exit 1
fi

REPO_ROOT="$(pwd)"

YES=0
PURGE_ENV=0
for arg in "$@"; do
  case "$arg" in
    -y | --yes) YES=1 ;;
    --purge-env) PURGE_ENV=1 ;;
    *)
      echo "Unknown option: $arg" >&2
      exit 1
      ;;
  esac
done

TARGETS=()
[ -d "$REPO_ROOT/.venv" ] && TARGETS+=("$REPO_ROOT/.venv")
[ -f "$REPO_ROOT/graphtr-out/graph.sqlite" ] && TARGETS+=("$REPO_ROOT/graphtr-out/graph.sqlite")
[ -f "$REPO_ROOT/graphtr-out/usage.sqlite" ] && TARGETS+=("$REPO_ROOT/graphtr-out/usage.sqlite")
[ -d "$REPO_ROOT/graphtr-out/qdrant" ] && TARGETS+=("$REPO_ROOT/graphtr-out/qdrant")
if [ "$PURGE_ENV" -eq 1 ] && [ -f "$REPO_ROOT/.env" ]; then
  TARGETS+=("$REPO_ROOT/.env")
fi

if [ ${#TARGETS[@]} -eq 0 ]; then
  echo "Nothing to remove."
  exit 0
fi

echo "Will remove:"
for t in "${TARGETS[@]}"; do
  echo "  $t"
done

if [ "$YES" -ne 1 ]; then
  read -r -p "Proceed? [y/N] " reply
  case "$reply" in
    y | Y | yes | YES) ;;
    *)
      echo "Aborted."
      exit 1
      ;;
  esac
fi

for t in "${TARGETS[@]}"; do
  rm -rf "$t"
  echo "Removed $t"
done

echo ""
echo "Done. The git checkout and any files outside .venv/.env/graphtr-out data are untouched."
