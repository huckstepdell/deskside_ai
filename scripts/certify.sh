#!/usr/bin/env bash
# Certify a candidate model for the demo lineup.
#
#   scripts/certify.sh <ollama-tag>
#
# Runs the measurement gauntlet this repo's recommendations are based on:
#   1. Box Bench      — 14 code-graded accuracy tasks (deterministic)
#   2. Batch sample   — 3 support tickets, reasoning off (throughput + style)
#   3. Case analysis  — the planted-contradiction test (analytical quality)
# Prints a verdict table. Read the numbers, then decide which role (if any)
# the model earns in models.json — the script measures, you judge.
set -euo pipefail
cd "$(dirname "$0")/.."
MODEL="${1:?usage: scripts/certify.sh <ollama-tag>}"
API=http://localhost:8080

curl -sf "$API/api/device" >/dev/null || { echo "Demo server not running (start the dashboard stack first)"; exit 1; }

if ! ollama list | awk '{print $1}' | grep -qxe "$MODEL" -e "$MODEL:latest"; then
  echo "==> Pulling $MODEL"
  ollama pull "$MODEL"
fi

echo "==> [1/3] Box Bench (accuracy, deterministic)"
BENCH=$(curl -sN -X POST "$API/api/bench/run" -H 'Content-Type: application/json' \
  -d "{\"host\":\"gb10\",\"model\":\"$MODEL\",\"reasoning\":\"default\"}" --max-time 1800 \
  | grep -E '"type": "(done|error)"' | tail -1 | sed 's/^data: //')

echo "==> [2/3] Batch sample (3 tickets, reasoning off)"
BATCH=$(curl -sN -X POST "$API/api/batch/run" -H 'Content-Type: application/json' \
  -d "{\"job\":\"tickets\",\"count\":3,\"model\":\"$MODEL\",\"reasoning\":false}" --max-time 1800 \
  | grep -E '"type": "(done|error)"' | tail -1 | sed 's/^data: //')

echo "==> [3/3] Case analysis (planted contradiction)"
CASE=$(curl -sN -X POST "$API/api/casework/run" -H 'Content-Type: application/json' \
  -d "{\"stage\":\"analysis\",\"model\":\"$MODEL\"}" --max-time 1800 \
  | grep -E '"type": "(result|error)"' | tail -1 | sed 's/^data: //')

python3 - "$MODEL" <<PY
import json, sys
model = sys.argv[1]

def load(s):
    try: return json.loads(s or "{}")
    except ValueError: return {}

bench, batch, case = load('''$BENCH'''), load('''$BATCH'''), load('''$CASE''')
print()
print(f"════════ Certification: {model} ════════")
if bench.get("type") == "done":
    print(f"  Accuracy   : {bench['score']}/{bench['total']} in {round(bench['seconds'])}s "
          f"({bench['tokens']} tokens)")
else:
    print(f"  Accuracy   : FAILED — {bench.get('error', 'no result')}")
if batch.get("type") == "done":
    per = batch["seconds"] / batch["items"]
    print(f"  Batch      : {per:.1f}s/item, {batch['run_out']} output tokens "
          f"(reference: coder ≈ 4s/item)")
else:
    print(f"  Batch      : FAILED — {batch.get('error', 'no result')}")
if case.get("type") == "result":
    d = case["data"]
    flags = d.get("contradictions", [])
    hit = any("22:1" in json.dumps(f) or "21:58" in json.dumps(f) for f in flags)
    print(f"  Analysis   : {len(d.get('timeline', []))} timeline events, "
          f"{len(flags)} contradiction(s) — planted Webb conflict "
          f"{'FOUND' if hit else 'MISSED'}")
else:
    print(f"  Analysis   : FAILED — {case.get('error', 'no result')}")
print()
print("  Reference scores (this hardware): nemotron 12-13/14 · coder 13/14 ·")
print("  e4b 13/14. Roles: workhorse needs the contradiction FOUND; fast needs")
print("  ~≤5s/item batch; a reasoner should beat the workhorse's accuracy.")
print("  If it earns a place: edit models.json (catalog entry + role/tier).")
PY
