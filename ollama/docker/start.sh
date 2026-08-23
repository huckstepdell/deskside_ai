export OLLAMA_BIND_IP=100.94.55.35
export PULL_MODELS="qwen3-coder-next:q4_K_M devstral-small-2:24b gpt-oss:latest"
mkdir -p "/container_data/ollama/ollama-data"
mkdir -p "/container_data/open-webui/open-webui-data"
docker compose up -d
