#!/bin/bash
# Rebuild và chạy lại docker-graphtr (hoton-graphtr + qdrant).
# Usage: ./rebuild.sh [-t|--tag VERSION]   (mặc định tag: latest)

set -e
set -o pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

IMAGE_TAG="latest"
while [[ $# -gt 0 ]]; do
    case "$1" in
        -t|--tag)
            IMAGE_TAG="$2"
            shift 2
            ;;
        *)
            echo -e "${RED}[ERROR] Tham số không hợp lệ: $1${NC}"
            echo "Usage: $0 [-t|--tag VERSION]"
            exit 1
            ;;
    esac
done
export IMAGE_TAG

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
COMPOSE_FILE="$SCRIPT_DIR/docker-compose.yml"

cd "$REPO_ROOT"

echo -e "${YELLOW}[1/2] Dừng container cũ...${NC}"
docker compose -f "$COMPOSE_FILE" down

echo -e "${YELLOW}[2/2] Build image tag '${IMAGE_TAG}' và chạy...${NC}"
docker compose -f "$COMPOSE_FILE" up --build -d

echo -e "${GREEN}Xong. Image: hoton-graphtr:${IMAGE_TAG}${NC}"
echo -e "${GREEN}Xem log: docker compose -f docker-graphtr/docker-compose.yml logs -f hoton-graphtr${NC}"
