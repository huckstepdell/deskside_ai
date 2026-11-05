# Dell AI Demo

A local, customer-facing demo environment for Dell AI hardware (**Pro Max GB10**, RTX 4070 and Blackwell 4000 nodes). One
window, zero terminal: pre-built AI demos plus live silicon telemetry, so an
audience can watch the GPU, unified memory, and power respond to real AI
workloads in real time.

Everything runs on your own hardware — no cloud, no internet needed during a demo.

## Deploy

One image (based on `ollama/ollama:latest`) runs in two roles: a compute node (`compose.node.yml`,
Ollama plus a metrics agent) and a dashboard (`compose.ui.yml`). Pick the profile that matches
each compute machine — it sets the model roles and what gets pulled:

| Machine | Profile |
|---|---|
| GB10 (arm64) | `gb10` (default) |
| Debian + 12GB RTX 4070 | `rtx4070` |
| Windows + 16GB Blackwell 4000 | `blackwell4000` |

Requirements: Docker plus the NVIDIA Container Toolkit (Linux), or Docker Desktop with WSL2 GPU
support (Windows). On the GB10, `scripts/deploy-compute.sh <profile>` and `scripts/deploy-ui.sh` build and restart the stacks without Dockhand.
`DEMO_PULL=none` (or a space-separated tag list) skips/overrides the automatic model pull. Models
persist in the `ollama` volume; the dashboard's state is in `./data`.

To update, redeploy the stack in Dockhand (it pulls the latest commit and rebuilds), or on the
GB10 run `PULL_GIT=1 scripts/deploy-compute.sh <profile>`.

### Split deployment: compute nodes + one dashboard

With Dockhand: add each machine's stack from this Git repo (`compose.node.yml` on GPU boxes,
`compose.ui.yml` for the dashboard), enable build at deploy, and set the variables below in the
stack environment. Redeploying pulls the latest commit and rebuilds.

Run `compose.node.yml` on each GPU machine (Ollama plus a small metrics agent, no UI) and
`compose.ui.yml` anywhere on the tailnet (no GPU needed):

```bash
# on each GPU machine (BIND_IP optionally pins ports to the tailnet IP)
DEMO_PROFILE=blackwell4000 DEVICE_NAME="Blackwell 4000" docker compose -f compose.node.yml up -d --build
# on the dashboard machine
OLLAMA_URL=http://pro-max-plus:11435 TELEMETRY_URL=http://pro-max-plus:9101 docker compose -f compose.ui.yml up -d --build
```

- The agent (port 9101) streams the node's real GPU, CPU, memory and power to the dashboard,
  so the telemetry strip shows the node, not the UI machine. NVML works in containers via the
  NVIDIA Container Toolkit; CPU/memory are the host's on Linux. On Windows (Docker Desktop) they
  are the WSL2 VM's, so memory is capped by `.wslconfig`, not the full 128GB.
- Each profile sets Ollama's own memory limits (`OLLAMA_MAX_LOADED_MODELS`, `OLLAMA_NUM_PARALLEL`,
  `OLLAMA_KEEP_ALIVE`, `OLLAMA_GPU_OVERHEAD`) under `env` in `profiles/<name>.json`; Ollama's
  scheduler then handles VRAM on the discrete-GPU nodes. Set any of them on the container to override.
- Extra nodes go in the `DEMO_HOSTS` variable (JSON) with `url` plus optional `telemetry_url`, for
  side-by-side comparison; wattage then comes from each node's agent instead of ssh.
- Remote nodes skip the dashboard's local memory guard and measured-load figures, which read
  the dashboard machine's `/proc`. Aperture isn't involved: the dashboard needs Ollama's native API.

## The pages

| Page | What it shows | The talking point |
|---|---|---|
| **Overview** | Specs, live GPU/memory/power/CPU charts, readiness lights, unified-memory bar, manual model load/unload dock | "A petaFLOP on a desk" |
| **Chat** | Streaming chat with local LLMs, live tokens/sec | "ChatGPT-class, fully private" |
| **Model Comparison** | Two modes. *Free prompt:* two models (or two *machines*) side by side — first-token, speed, reasoning vs answer tokens, cost, live wattage + tokens/Wh, plus the memory and context the load actually took. Each pane picks a *loadout* (context window, thinking on/off), and its spec line (MoE or dense, parameters, quantization, max context) is read from the model file itself. *Ladder:* a fixed 8 GB / 76 GB pairing climbs four tasks of rising difficulty, both resident at once, every ✓/✗ stamped by a Python checker | "You choose the trade-offs — and one box holds both choices" |
| **Box Bench** | 14 code-graded accuracy tasks, deterministic scores, leaderboard | "Quality, with receipts" |
| **Build-Off** | Two models build the same brief; results rendered live | "A website for a nickel of tokens" |
| **Vision** | Drop in a photo, the vision model describes it | "The AI can see — locally" |
| **Image Studio** | Stable Diffusion XL text-to-image with step progress | GPU meter pins at 100% |
| **Docs Q&A** | Upload a PDF, ask questions, see cited passages | "Data never leaves the building" |
| **Batch Works** | Bulk jobs (ticket triage, marketing copy) with a live ticker and reasoning toggle | "Your nightly batch job, at desk-side prices" |
| **Agent Works** | One task, three ways: one-shot, everything-pasted-in, and a real tool-calling agent loop — with the step trace, the findings it records, and all three token bills | "This is why agentic AI needs its own hardware" |
| **AI Agent** | NVIDIA NemoClaw status + launch (OpenClaw in an OpenShell sandbox) | "An always-on agent, sandboxed" |
| **Runbooks** | Every NVIDIA DGX Spark playbook in the local clone, flagships pinned | "Ready to go, already downloaded" |
| **Verticals** | Healthcare, Public Safety (+ Case Works workflow), Legal, Support, Field Service — scenario pages with bundled fictional sample data | Industry aha moments |

The telemetry strip on top is always visible — every demo visibly moves the
silicon — and the **Cloud savings** tile counts every locally generated token
against cloud API rates, climbing live and persisting across sessions.

## Configuration

- **`models.json`** (tracked) — curated model catalog: labels, traits, role
  recommendations (workhorse / vision / fast / reasoner), install tiers.
  Any installed Ollama model appears in the dashboard automatically; this
  file only adds the polish. Vet candidates with `scripts/certify.sh`.
- **`DEMO_HOSTS`** (stack variable; see `demo-config.example.json` for the shape) —
  companion inference hosts and role overrides.

## Architecture

- `server/` — FastAPI backend: telemetry sampler (NVML + /proc → SSE),
  streaming proxy to Ollama (local + configured companion hosts), unified-
  memory guard (evicts idle models before loads that wouldn't fit),
  per-request reasoning control, in-process SDXL, in-memory RAG, structured-
  output Case Works stages, code-graded bench, token-savings ledger.
- `static/` — vanilla JS single-page frontend; Chart.js and marked.js
  vendored locally (no CDN — works fully offline).
- `docs/` — PDFs auto-indexed into Docs Q&A at startup. Bundled samples are
  clearly fictional; drop real ones in freely (they stay local).
- `agent-workspace/` — the read-only file tree Agent Works points its agent at,
  one folder per task. Everything in it is fictional. Tools are confined to
  this directory and can only read, list, search, and add up — an agent can't
  surprise you mid-demo.

## Before a customer call (2 minutes)

1. Check the **Demo readiness** lights on Overview — all green.
2. Warm up what you'll use: **Warm up** in Image Studio, or load your chat
   model from the **Model dock**.
3. Optional: upload the customer's PDF in Docs Q&A ahead of time.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Red "AI engine" light | Check the compute node's container is running (`docker compose -f compose.node.yml ps`) and that `OLLAMA_URL` is reachable from the dashboard container |
| First reply slow, "Loading the model into memory…" | Normal cold load — pre-warm via the Model dock |
| "Can't reach the demo server" | Check the dashboard container is running; restart the stack in Dockhand |
| Companion box offline in Model Comparison | Check its Ollama is running and reachable, and that its entry in `DEMO_HOSTS` is correct |
| Ladder mode's run button is greyed out | It needs both audited models — the page prints the `ollama pull` line to run |
| Telemetry flat at zero | `nvidia-smi` to confirm the driver is up |
