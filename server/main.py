"""Dell AI Demo — FastAPI backend.

Run:  uvicorn server.main:app --host 0.0.0.0 --port 8080 (the container runs this)
"""

import asyncio
import base64
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import (agentworks, batchworks, bench, casework, chat, config, imagegen, ladder,
               rag, runbooks, scenarios, telemetry, tokens)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
logger = logging.getLogger(__name__)


async def _download_sdxl_model():
    try:
        await asyncio.to_thread(imagegen.download_model)
    except Exception:
        logger.exception("Optional SDXL model download failed")


async def _sdxl_idle_reaper():
    """Return SDXL's unified memory to the pool when Image Studio sits unused."""
    while True:
        await asyncio.sleep(60)
        imagegen.unload_if_idle()


@asynccontextmanager
async def lifespan(app: FastAPI):
    telemetry.start_sampler()
    await rag.load_preloaded_docs()
    if os.environ.get("DOWNLOAD_IMAGEGEN") == "1":
        asyncio.create_task(_download_sdxl_model())
    reaper = asyncio.create_task(_sdxl_idle_reaper())
    yield
    reaper.cancel()
    await telemetry.stop_sampler()


app = FastAPI(title="Dell AI Demo", lifespan=lifespan)

SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


# ---------- Telemetry ----------

async def _proxy_sse(url: str):
    """Relay a node agent's SSE stream; a dead node ends the stream and the
    browser's EventSource reconnects on its own."""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(None, connect=5)) as client:
            async with client.stream("GET", url) as r:
                async for chunk in r.aiter_raw():
                    yield chunk
    except httpx.HTTPError:
        return


@app.get("/api/telemetry/stream")
async def telemetry_stream():
    if config.TELEMETRY_URL:
        return StreamingResponse(_proxy_sse(f"{config.TELEMETRY_URL}/api/telemetry/stream"),
                                 media_type="text/event-stream", headers=SSE_HEADERS)
    return StreamingResponse(telemetry.sse_stream(), media_type="text/event-stream", headers=SSE_HEADERS)


@app.get("/api/device")
async def device():
    if config.TELEMETRY_URL:
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(f"{config.TELEMETRY_URL}/api/device", timeout=5)
                r.raise_for_status()
                return r.json()
        except httpx.HTTPError:
            pass
    return telemetry.device_info()


@app.get("/api/config")
async def client_config():
    """Deployment facts the frontend needs: role models and remote hosts."""
    return {"roles": config.ROLES,
            "remote_hosts": [k for k in config.HOSTS if k != "gb10"]}


@app.get("/api/tokens")
async def token_savings():
    """Cumulative local tokens/images and the estimated cloud spend avoided."""
    return tokens.summary()


@app.get("/api/memory")
async def memory_view():
    """What's occupying the unified memory: loaded models, everything else, free."""
    import psutil

    mem = psutil.virtual_memory()
    models = []
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{chat.OLLAMA}/api/ps", timeout=5)
            r.raise_for_status()
            for m in r.json().get("models", []):
                meta = chat.MODEL_CATALOG.get(m["name"], {})
                label = meta.get("label", m["name"])
                if m["name"].startswith(chat.EMBED_MODEL):
                    label = "Docs Q&A index (embeddings)"
                models.append({
                    "name": m["name"],
                    "label": label,
                    "size_gb": round(m["size"] / 2**30, 1),
                    "source": "Ollama",
                    "expires_at": m.get("expires_at"),
                })
    except httpx.HTTPError:
        pass
    sdxl_gb = imagegen.loaded_memory_gb()
    if sdxl_gb:
        models.append({
            "name": "sdxl", "label": "Stable Diffusion XL",
            "size_gb": sdxl_gb, "source": "Image Studio", "expires_at": None,
        })
    return {
        "total_gb": round(mem.total / 2**30, 1),
        "used_gb": round((mem.total - mem.available) / 2**30, 1),
        "models": models,
    }


@app.get("/api/preflight")
async def preflight():
    """Readiness lights for the Overview page — check before a customer call."""
    checks = {"ollama": False, "chat_models": [], "vision_model": False, "embed_model": False}
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{chat.OLLAMA}/api/tags", timeout=5)
            r.raise_for_status()
            installed = [m["name"] for m in r.json().get("models", [])]
            checks["ollama"] = True
            # every chat-capable model counts, catalog or not — a pulled model
            # is demoable immediately, so the readiness light should say so
            checks["chat_models"] = [m for m in installed
                                     if not m.startswith(chat.EMBED_MODEL)]
            checks["vision_model"] = chat.VISION_MODEL in installed
            checks["embed_model"] = any(m.startswith(chat.EMBED_MODEL) for m in installed)
    except httpx.HTTPError:
        pass
    checks["sdxl_cached"] = imagegen.is_cached()
    checks["sdxl_installed"] = imagegen.is_installed()
    checks["sdxl_loaded"] = imagegen.is_loaded()
    checks["runbooks"] = sum(1 for r_ in runbooks.list_runbooks() if r_["available"])
    checks["rag_docs"] = len(rag.list_docs())
    return checks


# ---------- Chat / Vision ----------

@app.get("/api/models")
async def models():
    try:
        return await chat.list_models()
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Ollama unreachable: {e}")


# ---------- Inference hosts (cross-machine comparison) ----------

@app.get("/api/hosts")
async def hosts():
    """Every registered inference box with its online state and models."""
    out = []
    async with httpx.AsyncClient() as client:
        for key, cfg in chat.HOSTS.items():
            entry = {"key": key, "label": cfg["label"], "local": cfg["local"],
                     "online": False, "models": []}
            try:
                r = await client.get(f"{cfg['url']}/api/tags", timeout=4)
                r.raise_for_status()
                entry["online"] = True
                for m in sorted(r.json().get("models", []), key=lambda x: x["name"]):
                    if m["name"].startswith(chat.EMBED_MODEL):
                        continue
                    meta = chat.MODEL_CATALOG.get(m["name"], {})
                    entry["models"].append({
                        "name": m["name"],
                        "label": meta.get("label", m["name"]),
                        "traits": meta.get("traits", ""),
                        "size_gb": round(m["size"] / 2**30, 1),
                    })
            except httpx.HTTPError:
                pass
            out.append(entry)
    return out


@app.get("/api/models/info")
async def model_info(host: str, name: str):
    """The model's real spec, read from its model file (MoE/dense, params,
    quantization, max context) plus the host's default context window."""
    if host not in chat.HOSTS:
        raise HTTPException(400, f"Unknown host: {host}")
    cfg = chat.HOSTS[host]
    try:
        async with httpx.AsyncClient() as client:
            info = await chat.model_info(client, name, cfg["url"])
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise HTTPException(404, f"Not installed on {host}: {name}")
        raise HTTPException(502, f"Ollama error: {e}")
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Ollama unreachable: {e}")
    # remote boxes' server config isn't visible from here
    default = await asyncio.to_thread(chat.local_default_context) if cfg["local"] else None
    return {**info, "default_context": default, "host": host}


@app.get("/api/power/{host_key}")
async def host_power(host_key: str):
    """Instant GPU power draw for an inference host, in watts.

    gb10 reads the local telemetry sampler. Remote hosts are polled with
    nvidia-smi over ssh (BatchMode — requires an authorized key); watts is
    null until that's set up, and the UI says so instead of guessing."""
    if host_key not in chat.HOSTS:
        raise HTTPException(404, f"Unknown host: {host_key}")
    agent = chat.HOSTS[host_key].get("telemetry_url")
    if agent:
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(f"{agent}/api/telemetry/latest", timeout=3)
                r.raise_for_status()
                return {"watts": r.json().get("watts")}
        except (httpx.HTTPError, ValueError):
            return {"watts": None}
    if host_key == "gb10":
        return {"watts": telemetry.latest_gpu_power()}
    ssh_alias = chat.HOSTS[host_key].get("ssh_power")
    if not ssh_alias:
        return {"watts": None}
    try:
        proc = await asyncio.create_subprocess_exec(
            "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=3",
            "-o", "StrictHostKeyChecking=accept-new", ssh_alias,
            "nvidia-smi", "--query-gpu=power.draw", "--format=csv,noheader,nounits",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        if proc.returncode == 0:
            watts = sum(float(x) for x in out.decode().split() if x.strip())
            return {"watts": round(watts, 1)}
    except (OSError, ValueError, asyncio.TimeoutError):
        pass
    return {"watts": None}


# ---------- Model dock (manual load / unload on the Overview page) ----------

class ModelAction(BaseModel):
    name: str
    options: dict | None = None  # Ollama load options — num_ctx is what matters


# Loads take minutes for big models — holding the HTTP request open that long
# gets killed as a network error by browsers. So load/unload run as background
# tasks and the dock reads progress from /api/models/status polls.
_dock_busy: dict[str, str] = {}    # model name → "loading" | "unloading"
_dock_errors: dict[str, str] = {}  # model name → last failure, shown in the UI


@app.get("/api/models/status")
async def models_status():
    """Every dock-controllable model with whether it's resident right now."""
    try:
        async with httpx.AsyncClient() as client:
            tags = (await client.get(f"{chat.OLLAMA}/api/tags", timeout=10)).json().get("models", [])
            ps = {m["name"]: m for m in (await client.get(f"{chat.OLLAMA}/api/ps", timeout=10)).json().get("models", [])}
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Ollama unreachable: {e}")
    installed = {m["name"]: m for m in tags}
    out = []

    def row(name: str, label: str) -> dict:
        return {
            "name": name,
            "label": label,
            "size_gb": round(installed[name]["size"] / 2**30, 1),
            "loaded": name in ps,
            "context": ps.get(name, {}).get("context_length"),
            "expires_at": ps.get(name, {}).get("expires_at"),
            "busy": _dock_busy.get(name),
            "error": _dock_errors.get(name),
        }

    # curated models first, in catalog order
    for name, meta in chat.MODEL_CATALOG.items():
        if name in installed:
            out.append(row(name, meta["label"]))
    # then anything else pulled onto the box, so a fresh `ollama pull` is
    # dock-controllable without a catalog edit (embeddings stay hidden —
    # they're loaded on demand by RAG, not by the presenter)
    for name in sorted(installed):
        if name in chat.MODEL_CATALOG or name.startswith(chat.EMBED_MODEL):
            continue
        out.append(row(name, name))

    out.append({
        "name": "sdxl",
        "label": "Stable Diffusion XL",
        "size_gb": 6.9,
        "loaded": imagegen.is_loaded(),
        "cached": imagegen.is_cached(),
        "expires_at": None,
        "busy": _dock_busy.get("sdxl"),
        "error": _dock_errors.get("sdxl"),
    })
    return out


async def _dock_load(name: str, options: dict | None = None):
    try:
        if name == "sdxl":
            await _make_room_for_sdxl()
            await imagegen.warmup()
            return
        async with httpx.AsyncClient(timeout=httpx.Timeout(1200, connect=10)) as client:
            options = {k: v for k, v in (options or {}).items()
                       if k in chat.CHAT_OPTIONS and v is not None}
            err = await chat.ensure_room(client, name, options.get("num_ctx"))
            if err:
                _dock_errors[name] = err
                return
            # empty generate = "load into memory"; generous keep_alive since
            # the presenter loaded it on purpose
            body = {"model": name, "keep_alive": "30m"}
            if options:
                body["options"] = options
            await chat.load_measured(client, body)
    except Exception as e:
        _dock_errors[name] = f"Load failed: {e}"
    finally:
        _dock_busy.pop(name, None)


async def _dock_unload(name: str):
    try:
        if name == "sdxl":
            if not await asyncio.to_thread(imagegen.unload):
                _dock_errors[name] = "SDXL is busy generating — try again when it finishes."
            return
        async with httpx.AsyncClient() as client:
            r = await client.post(f"{chat.OLLAMA}/api/generate",
                                  json={"model": name, "keep_alive": 0}, timeout=60)
            r.raise_for_status()
    except Exception as e:
        _dock_errors[name] = f"Unload failed: {e}"
    finally:
        _dock_busy.pop(name, None)


@app.post("/api/models/load")
async def model_load(req: ModelAction):
    # Gate on what's actually installed rather than on the curated catalog:
    # the dock lists freshly pulled models too, and they must be loadable.
    if req.name != "sdxl":
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(f"{chat.OLLAMA}/api/tags", timeout=10)
                r.raise_for_status()
        except httpx.HTTPError as e:
            raise HTTPException(502, f"Ollama unreachable: {e}")
        if req.name not in {m["name"] for m in r.json().get("models", [])}:
            raise HTTPException(404, f"Not installed: {req.name}")
    if req.name in _dock_busy:
        return {"started": False, "busy": _dock_busy[req.name]}
    _dock_errors.pop(req.name, None)
    _dock_busy[req.name] = "loading"
    asyncio.create_task(_dock_load(req.name, req.options))
    return {"started": True}


@app.post("/api/models/unload")
async def model_unload(req: ModelAction):
    if req.name in _dock_busy:
        return {"started": False, "busy": _dock_busy[req.name]}
    _dock_errors.pop(req.name, None)
    _dock_busy[req.name] = "unloading"
    asyncio.create_task(_dock_unload(req.name))
    return {"started": True}


class ChatRequest(BaseModel):
    model: str
    messages: list[dict]
    images: list[str] | None = None  # base64, attached to last user message
    host: str = "gb10"  # HOSTS registry key
    think: bool | None = None  # None = the model's default
    options: dict | None = None  # Ollama options, e.g. num_ctx


@app.post("/api/chat")
async def chat_endpoint(req: ChatRequest):
    if req.host not in chat.HOSTS:
        raise HTTPException(400, f"Unknown host: {req.host}")
    return StreamingResponse(
        chat.stream_chat(req.messages, req.model, req.images, think=req.think,
                         host=req.host, options=req.options),
        media_type="text/event-stream", headers=SSE_HEADERS,
    )


# ---------- Image generation ----------

class ImageGenRequest(BaseModel):
    prompt: str
    negative_prompt: str = ""
    steps: int = 30
    seed: int | None = None


async def _make_room_for_sdxl():
    """SDXL needs ~10GB; evict idle LLMs first if the pool is tight."""
    if not imagegen.is_loaded() and chat.mem_available_gb() < 15:
        async with httpx.AsyncClient() as client:
            await chat.free_memory(client, need_gb=15)


@app.post("/api/imagegen")
async def imagegen_endpoint(req: ImageGenRequest):
    steps = max(4, min(req.steps, 60))
    await _make_room_for_sdxl()
    return StreamingResponse(
        imagegen.generate_sse(req.prompt, req.negative_prompt, steps, req.seed),
        media_type="text/event-stream", headers=SSE_HEADERS,
    )


@app.post("/api/imagegen/warmup")
async def imagegen_warmup():
    await _make_room_for_sdxl()
    await imagegen.warmup()
    return {"loaded": True}


# ---------- Batch Works ----------

@app.get("/api/batch/jobs")
async def batch_jobs():
    return [{"key": k, "label": j["label"], "blurb": j["blurb"]} for k, j in batchworks.JOBS.items()]


class BatchRequest(BaseModel):
    job: str
    count: int = 25
    model: str = config.role("fast") or config.role("workhorse")
    reasoning: bool = False


@app.post("/api/batch/run")
async def batch_run(req: BatchRequest):
    count = max(1, min(req.count, 500))
    return StreamingResponse(
        batchworks.run(req.job, count, req.model, req.reasoning),
        media_type="text/event-stream", headers=SSE_HEADERS,
    )


# ---------- Agent Works (generative vs agentic) ----------

@app.get("/api/agentworks/tasks")
async def agentworks_tasks():
    return agentworks.task_list()


@app.get("/api/agentworks/files/{task_key}")
async def agentworks_files(task_key: str):
    """The workspace the agent is pointed at — shown so the audience can see
    the agent had to go find things, not that they were handed to it."""
    task = agentworks.TASKS.get(task_key)
    if task is None:
        raise HTTPException(404, "Unknown task")
    root = agentworks.WORKSPACE / task["folder"]
    return [{"path": str(p.relative_to(root)), "bytes": p.stat().st_size}
            for p in agentworks._workspace_files(task["folder"])]


class AgentWorksRequest(BaseModel):
    task: str
    mode: str = "agentic"  # agentic | generative | stuffed
    model: str = config.role("workhorse")
    reasoning: bool = False


@app.post("/api/agentworks/run")
async def agentworks_run(req: AgentWorksRequest):
    if req.mode in ("generative", "stuffed"):
        gen = agentworks.run_generative(req.task, req.model, stuffed=req.mode == "stuffed")
    else:
        gen = agentworks.run_agentic(req.task, req.model, req.reasoning)
    return StreamingResponse(gen, media_type="text/event-stream", headers=SSE_HEADERS)


# ---------- Ladder (two models, four rungs of rising difficulty) ----------

@app.get("/api/ladder/info")
async def ladder_info():
    """The fixed pairing, the four rungs, and whether both models are actually
    on the box — the page needs that to warn instead of offering a dead button."""
    return await ladder.info()


@app.post("/api/ladder/run")
async def ladder_run():
    return StreamingResponse(ladder.run(), media_type="text/event-stream",
                             headers=SSE_HEADERS)


# ---------- Case Works (public-safety workflow demo) ----------

@app.get("/api/casework/data")
async def casework_data():
    """Source material each stage displays before running."""
    return {
        "field_notes": casework.FIELD_NOTES,
        "bundle": casework.CASE_BUNDLE,
        "incidents": casework.INCIDENTS,
    }


class CaseworkRequest(BaseModel):
    stage: str  # report | analysis | patterns
    notes: str | None = None
    model: str = casework.DEFAULT_MODEL


@app.post("/api/casework/run")
async def casework_run(req: CaseworkRequest):
    return StreamingResponse(
        casework.run(req.stage, req.notes, req.model),
        media_type="text/event-stream", headers=SSE_HEADERS,
    )


# ---------- Docs Q&A (RAG) ----------

@app.get("/api/rag/docs")
async def rag_docs():
    return rag.list_docs()


@app.post("/api/rag/upload")
async def rag_upload(file: UploadFile):
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "Please upload a PDF file.")
    data = await file.read()
    try:
        return await rag.ingest_pdf(file.filename, data)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Embedding failed (Ollama): {e}")


class RagAskRequest(BaseModel):
    doc_id: str
    question: str
    model: str = config.role("workhorse")  # reasoning disabled below → snappy answers


@app.post("/api/rag/ask")
async def rag_ask(req: RagAskRequest):
    try:
        passages = await rag.retrieve(req.doc_id, req.question)
    except KeyError as e:
        raise HTTPException(404, str(e))
    doc_name = req.doc_id

    async def stream():
        import json as _json
        yield f"event: passages\ndata: {_json.dumps(passages)}\n\n"
        messages = rag.build_rag_messages(req.question, passages, doc_name)
        async for part in chat.stream_chat(messages, req.model, think=False):
            yield part

    return StreamingResponse(stream(), media_type="text/event-stream", headers=SSE_HEADERS)


# ---------- Box Bench (accuracy battery) ----------

@app.get("/api/bench/tasks")
async def bench_tasks():
    """The battery, prompts and grading included — transparency is the point."""
    return [{"id": t["id"], "category": t["category"], "label": t["label"],
             "prompt": t["prompt"],
             "why": bench.WHY.get(t["id"], ""),
             "graded": bench.grading_text(t["check"])} for t in bench.TASKS]


@app.get("/api/bench/runs")
async def bench_runs():
    return bench.list_runs()


class BenchRequest(BaseModel):
    host: str = "gb10"
    model: str
    reasoning: str = "default"  # default | off | on


@app.post("/api/bench/run")
async def bench_run(req: BenchRequest):
    if req.host not in chat.HOSTS:
        raise HTTPException(400, f"Unknown host: {req.host}")
    return StreamingResponse(
        bench.run(req.host, req.model, req.reasoning),
        media_type="text/event-stream", headers=SSE_HEADERS,
    )


# ---------- AI Agent (NemoClaw sandbox) ----------

NEMOCLAW_UI_URL = "http://127.0.0.1:18789/"
_agent_url_cache = {"url": "", "at": 0.0}


async def _agent_ui_url(sandbox: str) -> str:
    """Authenticated control-UI URL (carries #token=…); cached 10 minutes.

    The bare 18789 page loads its shell but can't reach the gateway without
    the token — which looks like 'failed to load' in the browser."""
    import os
    import time as _time

    if _agent_url_cache["url"] and _time.time() - _agent_url_cache["at"] < 600:
        return _agent_url_cache["url"]
    import shutil
    cli = shutil.which("nemoclaw") or str(Path.home() / ".local/bin/nemoclaw")
    try:
        proc = await asyncio.create_subprocess_exec(
            cli, sandbox, "dashboard-url", "--quiet",
            env={**os.environ, "NEMOCLAW_GATEWAY_PORT": "18080"},
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        url = out.decode().strip().splitlines()[-1] if out.strip() else ""
        if proc.returncode == 0 and url.startswith("http"):
            _agent_url_cache.update(url=url, at=_time.time())
            return url
    except (OSError, asyncio.TimeoutError):
        pass
    return NEMOCLAW_UI_URL


@app.get("/api/agent/status")
async def agent_status():
    """Light-weight NemoClaw status: sandbox config + whether its UI is up."""
    import json as _json

    info = {"configured": False, "ui_up": False, "model": None,
            "agent": None, "sandbox": None, "ui_url": NEMOCLAW_UI_URL}
    try:
        data = _json.loads((Path.home() / ".nemoclaw" / "sandboxes.json").read_text())
        name = data.get("defaultSandbox", "")
        sb = data.get("sandboxes", {}).get(name, {})
        if sb:
            info.update(configured=True, sandbox=name, model=sb.get("model"),
                        agent=sb.get("agent") or "openclaw")
    except (OSError, ValueError):
        pass
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(NEMOCLAW_UI_URL, timeout=3)
            info["ui_up"] = r.status_code < 500
    except httpx.HTTPError:
        pass
    if info["configured"] and info["ui_up"]:
        info["ui_url"] = await _agent_ui_url(info["sandbox"])
    return info


# ---------- Scenarios (verticals + Scenario Studio) ----------

@app.get("/api/scenarios")
async def scenarios_list():
    return scenarios.list_scenarios()


@app.post("/api/scenarios")
async def scenarios_save(s: dict):
    try:
        return scenarios.save_custom(s)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.delete("/api/scenarios/{sid}")
async def scenarios_delete(sid: str):
    if not scenarios.delete_custom(sid):
        raise HTTPException(404, "Not a deletable custom scenario")
    return {"ok": True}


class ScenarioGenRequest(BaseModel):
    industry: str
    workflows: str
    sensitive: str = ""
    extra: str = ""


@app.post("/api/scenarios/generate")
async def scenarios_generate(req: ScenarioGenRequest):
    return StreamingResponse(
        scenarios.generate_scenario(req.industry, req.workflows, req.sensitive, req.extra),
        media_type="text/event-stream", headers=SSE_HEADERS,
    )


@app.post("/api/scenarios/gendoc")
async def scenarios_gendoc(s: dict):
    return StreamingResponse(
        scenarios.generate_doc(s),
        media_type="text/event-stream", headers=SSE_HEADERS,
    )


class DocGenRequest(BaseModel):
    prompt: str


@app.post("/api/docgen")
async def docgen(req: DocGenRequest):
    return StreamingResponse(
        scenarios.generate_docout(req.prompt),
        media_type="text/event-stream", headers=SSE_HEADERS,
    )


@app.post("/api/docgen/pdf")
async def docgen_pdf(doc: dict):
    pdf = scenarios.build_docout_pdf(doc)
    from fastapi.responses import Response
    return Response(content=pdf, media_type="application/pdf", headers={
        "Content-Disposition": 'attachment; filename="demo-document-FICTIONAL.pdf"'})


@app.get("/api/scenarios/{sid}/export")
async def scenarios_export(sid: str):
    match = [s for s in scenarios.list_scenarios() if s["id"] == sid]
    if not match:
        raise HTTPException(404, "Unknown scenario")
    bundle = {"scenario": match[0]}
    pdf = scenarios.DOC_DIR / f"{sid}-SAMPLE.pdf"
    if pdf.exists():
        bundle["doc_pdf_b64"] = base64.b64encode(pdf.read_bytes()).decode()
        bundle["doc_name"] = pdf.name
    return JSONResponse(bundle, headers={
        "Content-Disposition": f'attachment; filename="{sid}.scenario.json"'})


@app.post("/api/scenarios/import")
async def scenarios_import(bundle: dict):
    s = bundle.get("scenario")
    if not isinstance(s, dict):
        raise HTTPException(400, "Not a scenario bundle")
    try:
        saved = scenarios.save_custom(s)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if bundle.get("doc_pdf_b64"):
        scenarios.DOC_DIR.mkdir(parents=True, exist_ok=True)
        (scenarios.DOC_DIR / f"{saved['id']}-SAMPLE.pdf").write_bytes(
            base64.b64decode(bundle["doc_pdf_b64"]))
        await rag.load_preloaded_docs()
    return saved


# ---------- Runbooks ----------

@app.get("/api/runbooks")
async def runbooks_list():
    return runbooks.list_runbooks()


@app.get("/api/runbooks/{slug}")
async def runbook_detail(slug: str):
    try:
        return JSONResponse({"slug": slug, "markdown": runbooks.get_runbook_markdown(slug)})
    except (KeyError, FileNotFoundError) as e:
        raise HTTPException(404, str(e))


# ---------- Static frontend ----------

@app.middleware("http")
async def no_stale_ui(request, call_next):
    """Always revalidate UI assets so browsers never run a stale app.js."""
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static"):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
# the bundled sample PDFs, viewable in-browser (linked from the vertical pages)
app.mount("/docs", StaticFiles(directory=STATIC_DIR.parent / "docs"), name="docs")
# generated scenario sample documents (created at runtime by the Studio)
scenarios.DOC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/scenario-docs", StaticFiles(directory=scenarios.DOC_DIR), name="scenario-docs")
