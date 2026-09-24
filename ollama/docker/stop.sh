#!/usr/bin/env bash

set -eu

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd -- "${SCRIPT_DIR}"

# PULL_MODELS is only required by compose for variable interpolation; value is irrelevant for teardown.
export PULL_MODELS="${PULL_MODELS:-unused}"
export CONTAINER_DATA_DIR="${CONTAINER_DATA_DIR:-/container_data}"

# Base file alone is sufficient: device overlays only patch env vars on the same services/containers.
docker compose -f docker-compose.yml down --remove-orphans
