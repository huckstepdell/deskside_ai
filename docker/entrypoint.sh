#!/usr/bin/env bash
# Runs Ollama and the dashboard in one container.
#   DEMO_MODE      all (default) | node (Ollama + metrics agent) | ui (dashboard only)
#   DEMO_PROFILE   gb10 | rtx4070 | blackwell4000 | none  (profiles/<name>.json)
#   DEMO_PULL      "auto" = pull the profile's model list, "none" = skip,
#                  or a space-separated list of model tags
set -uo pipefail
MODE="${DEMO_MODE:-all}"

PROFILE="${DEMO_PROFILE:-gb10}"
if [[ -f "/app/profiles/${PROFILE}.json" ]]; then
  cp "/app/profiles/${PROFILE}.json" /app/profiles/active.json
fi
# Ollama limits from the profile's "env"; variables set on the container win
if [[ -f /app/profiles/active.json ]]; then
  while IFS='=' read -r k v; do
    [[ -n "$k" && -z "${!k:-}" ]] && export "$k=$v"
  done < <(python -c "import json;[print(f'{k}={v}') for k,v in json.load(open('/app/profiles/active.json')).get('env',{}).items()]")
fi
# a user-mounted /app/demo-config.json (companion hosts) wins over the profile
for f in /app/config/demo-config.json /app/demo-config.json; do [[ -f $f ]] && export DEMO_CONFIG=$f; done

PIDS=()
trap 'kill "${PIDS[@]}" 2>/dev/null' TERM INT

if [[ "$MODE" != "ui" ]]; then
  ollama serve &
  PIDS+=($!)
  for _ in $(seq 1 60); do
    curl -sf http://localhost:11434/api/version >/dev/null && break
    sleep 1
  done

  pull_models() {
    local models="${DEMO_PULL:-auto}"
    [[ "$models" == "none" ]] && return
    if [[ "$models" == "auto" ]]; then
      models=$(python -c "import json;print(' '.join(json.load(open('/app/profiles/active.json')).get('pull',[])))" 2>/dev/null)
    fi
    for m in $models; do
      ollama list | awk '{print $1}' | grep -qxe "$m" -e "$m:latest" || ollama pull "$m"
    done
  }
  pull_models &
fi

if [[ "$MODE" == "node" ]]; then
  uvicorn server.agent:app --host 0.0.0.0 --port 9101 &
else
  uvicorn server.main:app --host 0.0.0.0 --port 8080 \
    --timeout-keep-alive 620 --timeout-graceful-shutdown 3 &
fi
PIDS+=($!)

wait -n "${PIDS[@]}"
kill "${PIDS[@]}" 2>/dev/null
wait
