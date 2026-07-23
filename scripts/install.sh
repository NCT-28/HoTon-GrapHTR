#!/usr/bin/env bash
# One-liner installer for a brand-new machine (no existing clone needed):
#
#   curl -fsSL https://raw.githubusercontent.com/NCT-28/HoTon-GrapHTR/main/scripts/install.sh | bash
#
# Custom target directory (note the `-s --` when piping args to a piped script):
#
#   curl -fsSL https://raw.githubusercontent.com/NCT-28/HoTon-GrapHTR/main/scripts/install.sh | bash -s -- my-dir
#
# Clones the repo (public, plain HTTPS, no auth needed), then delegates to
# scripts/setup_zero_service.sh inside the clone for the actual Python/venv setup.
set -euo pipefail

REPO_URL="${HOTON_GRAPHTR_REPO_URL:-https://github.com/NCT-28/HoTon-GrapHTR.git}"
TARGET_DIR="${1:-HoTon-GrapHTR}"

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

cd "$TARGET_DIR"
exec bash scripts/setup_zero_service.sh --run
