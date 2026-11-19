#!/usr/bin/env bash
# Runs vLLM and (in all/ui mode) the dashboard.
#   DEMO_MODE      all (default) | node (vLLM only; use compose.agent.yml for :9101 metrics) | ui (dashboard only)
#   DEMO_PROFILE   gb10 | rtx4070 | blackwell4000 | none  (profiles/<name>.json)
#   VLLM_MODEL     HuggingFace model ID or local path (overrides profile env)
set -uo pipefail
MODE="${DEMO_MODE:-all}"

PROFILE="${DEMO_PROFILE:-gb10}"
if [[ -f "/app/profiles/${PROFILE}.json" ]]; then
  cp "/app/profiles/${PROFILE}.json" /app/profiles/active.json
fi
# vLLM settings from the profile's "env"; variables set on the container win
if [[ -f /app/profiles/active.json ]]; then
  while IFS='=' read -r k v; do
    [[ -n "$k" && -z "${!k:-}" ]] && export "$k=$v"
  done < <(python3 -c "import json;[print(f'{k}={v}') for k,v in json.load(open('/app/profiles/active.json')).get('env',{}).items()]")
fi
# a user-mounted /app/demo-config.json (companion hosts) wins over the profile
for f in /app/config/demo-config.json /app/demo-config.json; do [[ -f $f ]] && export DEMO_CONFIG=$f; done

PIDS=()
trap 'kill "${PIDS[@]}" 2>/dev/null' TERM INT

if [[ "$MODE" != "ui" ]]; then
  if [[ -z "${VLLM_MODEL:-}" && -f /app/profiles/active.json ]]; then
    VLLM_MODEL=$(python3 -c "import json;d=json.load(open('/app/profiles/active.json'));print(d.get('roles',{}).get('workhorse',''))" 2>/dev/null || true)
  fi
  VLLM_MODEL="${VLLM_MODEL:-Qwen/Qwen2.5-7B-Instruct-AWQ}"

  VLLM_ARGS=(
    "$VLLM_MODEL"
    --host "${VLLM_HOST:-0.0.0.0}"
    --port "${VLLM_PORT:-8000}"
  )
  [[ -n "${VLLM_MAX_MODEL_LEN:-}" ]] && VLLM_ARGS+=(--max-model-len "$VLLM_MAX_MODEL_LEN")
  [[ -n "${VLLM_GPU_MEMORY_UTILIZATION:-}" ]] && VLLM_ARGS+=(--gpu-memory-utilization "$VLLM_GPU_MEMORY_UTILIZATION")
  [[ -n "${VLLM_KV_CACHE_DTYPE:-}" ]] && VLLM_ARGS+=(--kv-cache-dtype "$VLLM_KV_CACHE_DTYPE")
  [[ -n "${VLLM_DTYPE:-}" ]] && VLLM_ARGS+=(--dtype "$VLLM_DTYPE")
  [[ -n "${VLLM_MAX_NUM_SEQS:-}" ]] && VLLM_ARGS+=(--max-num-seqs "$VLLM_MAX_NUM_SEQS")
  [[ -n "${VLLM_QUANTIZATION:-}" ]] && VLLM_ARGS+=(--quantization "$VLLM_QUANTIZATION")
  [[ -n "${VLLM_TENSOR_PARALLEL_SIZE:-}" ]] && VLLM_ARGS+=(--tensor-parallel-size "$VLLM_TENSOR_PARALLEL_SIZE")
  [[ -n "${VLLM_REASONING_PARSER:-}" ]] && VLLM_ARGS+=(--reasoning-parser "$VLLM_REASONING_PARSER")
  if [[ -n "${VLLM_TOOL_CALL_PARSER:-}" ]]; then
    VLLM_ARGS+=(--enable-auto-tool-choice --tool-call-parser "$VLLM_TOOL_CALL_PARSER")
  fi
  if [[ "${VLLM_ENFORCE_EAGER:-0}" == "1" || "${VLLM_ENFORCE_EAGER:-}" == "true" ]]; then
    VLLM_ARGS+=(--enforce-eager)
  fi
  if [[ -n "${VLLM_EXTRA_ARGS:-}" ]]; then
    # shellcheck disable=SC2206
    EXTRA_ARR=($VLLM_EXTRA_ARGS)
    VLLM_ARGS+=("${EXTRA_ARR[@]}")
  fi

  if [[ "$MODE" == "node" ]]; then
    exec env \
      -u VLLM_MODEL -u VLLM_MAX_MODEL_LEN -u VLLM_GPU_MEMORY_UTILIZATION \
      -u VLLM_KV_CACHE_DTYPE -u VLLM_DTYPE -u VLLM_MAX_NUM_SEQS \
      -u VLLM_QUANTIZATION -u VLLM_TENSOR_PARALLEL_SIZE \
      -u VLLM_REASONING_PARSER -u VLLM_TOOL_CALL_PARSER \
      -u VLLM_ENFORCE_EAGER -u VLLM_EXTRA_ARGS -u VLLM_HOST -u VLLM_PORT \
      vllm serve "${VLLM_ARGS[@]}"
  fi

  env \
    -u VLLM_MODEL -u VLLM_MAX_MODEL_LEN -u VLLM_GPU_MEMORY_UTILIZATION \
    -u VLLM_KV_CACHE_DTYPE -u VLLM_DTYPE -u VLLM_MAX_NUM_SEQS \
    -u VLLM_QUANTIZATION -u VLLM_TENSOR_PARALLEL_SIZE \
    -u VLLM_REASONING_PARSER -u VLLM_TOOL_CALL_PARSER \
    -u VLLM_ENFORCE_EAGER -u VLLM_EXTRA_ARGS -u VLLM_HOST -u VLLM_PORT \
    vllm serve "${VLLM_ARGS[@]}" &
  PIDS+=($!)
fi

uvicorn server.main:app --host 0.0.0.0 --port 8080 \
  --timeout-keep-alive 620 --timeout-graceful-shutdown 3 &
PIDS+=($!)

wait -n "${PIDS[@]}"
kill "${PIDS[@]}" 2>/dev/null
wait
