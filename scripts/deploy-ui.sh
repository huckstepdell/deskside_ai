#!/usr/bin/env bash
# Build and (re)deploy the dashboard (compose.ui.yml) without Dockhand/Hawser.
# Run on the machine that should host the UI (no GPU needed):
#   VLLM_URL=http://<node>:8000 TELEMETRY_URL=http://<node>:9101 scripts/deploy-ui.sh
#
# Options (env):
#   VLLM_URL         required: the primary compute node's vLLM endpoint
#   VLLM_EMBED_URL   optional: dedicated vLLM embeddings endpoint (e.g. http://<node>:8001)
#   TELEMETRY_URL    the primary node's metrics agent
#   DEMO_PROFILE     profile for the primary node (default gb10)
#   PRIMARY_LABEL / DEVICE_NAME   display names
#   DEMO_HOSTS       extra comparison nodes as JSON (see demo-config.example.json)
#   INSTALL_IMAGEGEN=1   include Image Studio (SDXL, ~4GB of wheels)
#   NO_CACHE=1       rebuild without cache
#   PULL_GIT=1       git pull --ff-only before building
set -euo pipefail
COMPOSE_FILE=compose.ui.yml
source "$(dirname "$0")/_deploy-common.sh"

: "${VLLM_URL:?set VLLM_URL, e.g. VLLM_URL=http://gb10:8000}"

deploy_stack
echo "Dashboard: http://$(hostname -f 2>/dev/null || hostname):8080"
