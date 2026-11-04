# Sourced by deploy-compute.sh / deploy-ui.sh. Expects COMPOSE_FILE to be set.
cd "$(dirname "${BASH_SOURCE[0]}")/.."
command -v docker >/dev/null || { echo "docker not found" >&2; exit 1; }

deploy_stack() {
  [[ "${PULL_GIT:-0}" == 1 ]] && git pull --ff-only
  local build_args=()
  [[ "${NO_CACHE:-0}" == 1 ]] && build_args+=(--no-cache)
  docker compose -f "$COMPOSE_FILE" build "${build_args[@]}"
  docker compose -f "$COMPOSE_FILE" up -d --remove-orphans
  docker image prune -f >/dev/null
  docker compose -f "$COMPOSE_FILE" ps
}
