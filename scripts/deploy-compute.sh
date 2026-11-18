#!/usr/bin/env bash
# Build and (re)deploy the compute node (compose.node.yml: vLLM + metrics agent)
# on any GPU machine, without Dockhand/Hawser. Run on that machine:
#   scripts/deploy-compute.sh <profile>      # gb10 | rtx4070 | blackwell4000
#
# Options (env):
#   DEMO_PROFILE   same as the argument
#   DEVICE_NAME    name the dashboard shows for this machine
#   BIND_IP        pin published ports to one address (e.g. the tailnet IP)
#   VLLM_MODEL     override the profile's HuggingFace model ID
#   HF_TOKEN       HuggingFace token for gated models
#   NO_CACHE=1     rebuild without cache
#   PULL_GIT=1     git pull --ff-only before building
set -euo pipefail
COMPOSE_FILE=compose.node.yml
source "$(dirname "$0")/_deploy-common.sh"

export DEMO_PROFILE="${1:-${DEMO_PROFILE:-}}"
if [[ -z "$DEMO_PROFILE" || ! -f "profiles/${DEMO_PROFILE}.json" ]]; then
  echo "usage: scripts/deploy-compute.sh <profile>   (one of: $(cd profiles && ls *.json | grep -v '^active' | sed 's/\.json//' | paste -sd' '))" >&2
  exit 1
fi

deploy_stack
host="${BIND_IP:-$(hostname -f 2>/dev/null || hostname)}"
echo "Compute node '$DEMO_PROFILE' up: vLLM http://$host:8000, agent http://$host:9101"
