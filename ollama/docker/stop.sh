export PULL_MODELS="qwen3-coder-next:q4_K_M devstral-small-2:24b"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd -- "${SCRIPT_DIR}"
export CONTAINER_DATA_DIR="/container_data"
docker compose -f docker-compose.yml -f docker-compose.webui.yml down
