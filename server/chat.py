"""Streaming chat proxy to the local Ollama service."""

import asyncio
import json
import os
import re
import time
from pathlib import Path

import httpx

from . import tokens
from .config import CATALOG as MODEL_CATALOG
from .config import HOSTS, LOCAL_OLLAMA as OLLAMA, role

# On GB10 the GPU and OS share one unified memory pool, so an oversized model
# load doesn't fail cleanly — it swap-thrashes the whole box. Keep this much
# free for the OS, dashboard, browser, and KV-cache growth.
MEMORY_HEADROOM_GB = 10

VISION_MODEL = role("vision")
EMBED_MODEL = role("embed") or "nomic-embed-text"


async def list_models() -> list[dict]:
    """Models installed in Ollama, decorated with demo-friendly labels."""
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{OLLAMA}/api/tags", timeout=10)
        r.raise_for_status()
    installed = {m["name"]: m for m in r.json().get("models", [])}
    out = []
    # curated models first, in catalog order, with their friendly metadata
    for name, meta in MODEL_CATALOG.items():
        if name in installed:
            out.append({
                "name": name,
                "label": meta["label"],
                "blurb": meta["blurb"],
                "traits": meta.get("traits", ""),
                "size_gb": round(installed[name]["size"] / 2**30, 1),
            })
    # then everything else on the box — a freshly pulled model is demoable
    # immediately, no catalog edit required (embeddings stay hidden)
    for name, m in sorted(installed.items()):
        if name in MODEL_CATALOG or name.startswith(EMBED_MODEL):
            continue
        out.append({
            "name": name, "label": name, "blurb": "",
            "traits": "", "size_gb": round(m["size"] / 2**30, 1),
        })
    return out


_thinking_cache: dict[str, bool] = {}


async def supports_thinking(client: httpx.AsyncClient, model: str, url: str = OLLAMA) -> bool:
    """Whether Ollama accepts the `think` toggle for this model (cached)."""
    key = f"{url}|{model}"
    if key not in _thinking_cache:
        try:
            r = await client.post(f"{url}/api/show", json={"model": model}, timeout=10)
            r.raise_for_status()
            _thinking_cache[key] = "thinking" in r.json().get("capabilities", [])
        except httpx.HTTPError:
            return False  # don't cache a transient failure
    return _thinking_cache[key]


def mem_available_gb() -> float:
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) / 2**20
    return 0.0


async def _loaded_models(client: httpx.AsyncClient, url: str = OLLAMA) -> list[dict]:
    r = await client.get(f"{url}/api/ps", timeout=10)
    r.raise_for_status()
    return r.json().get("models", [])


async def free_memory(client: httpx.AsyncClient, need_gb: float, keep: set[str] = frozenset()) -> float:
    """Unload Ollama models (except `keep` and the embed model) until
    `need_gb` is available. Best effort; returns MemAvailable in GB."""
    if mem_available_gb() >= need_gb:
        return mem_available_gb()
    requested = False
    for m in await _loaded_models(client):
        name = m["name"]
        if name in keep or name.startswith(EMBED_MODEL):
            continue
        # keep_alive: 0 with no prompt = "unload this model now"
        await client.post(f"{OLLAMA}/api/generate", json={"model": name, "keep_alive": 0}, timeout=30)
        requested = True
    if requested:
        for _ in range(30):  # unloading tens of GB takes a few seconds
            if mem_available_gb() >= need_gb:
                break
            await asyncio.sleep(1)
    return mem_available_gb()


_info_cache: dict[str, dict] = {}


def _quant_bits(quant: str) -> int | None:
    """Q4_K_M → 4, IQ2_XXS → 2, F16 → 16, BF16 → 16."""
    m = re.match(r"(?:I?Q|B?F)(\d+)", (quant or "").upper())
    return int(m.group(1)) if m else None


def _params_label(n: int | None, fallback: str) -> str:
    if not n:
        return fallback or ""
    b = n / 1e9
    return f"{b:.0f}B" if b >= 10 else f"{b:.1f}B"


def ctx_label(n: int | None) -> str:
    """4096 → 4k, 131072 → 128k, 262144 → 262k, 1048576 → 1M (same rule as the UI)."""
    if not n:
        return ""
    if n >= 2**20 and n % 2**20 == 0:
        return f"{n // 2**20}M"
    return f"{round(n / (1000 if n > 131072 else 1024))}k"


async def model_info(client: httpx.AsyncClient, model: str, url: str = OLLAMA) -> dict:
    """A compact spec for `model`, read from the model file via /api/show.

    Cached per host; failures aren't cached (raises httpx.HTTPError)."""
    key = f"{url}|{model}"
    if key in _info_cache:
        return _info_cache[key]
    r = await client.post(f"{url}/api/show", json={"model": model}, timeout=10)
    r.raise_for_status()
    d = r.json()
    details, mi = d.get("details", {}), d.get("model_info", {}) or {}
    arch = mi.get("general.architecture") or details.get("family") or ""
    a = lambda k: mi.get(f"{arch}.{k}")  # noqa: E731
    layers = a("block_count")
    experts, used = a("expert_count"), a("expert_used_count")

    # KV cache bytes per token of context (f16 K and V): what each token of
    # num_ctx reserves up front. Hybrid Mamba models list per-layer KV heads
    # with zeros for the SSM layers; hybrids that report a single int mark
    # their attention layers with full_attention_interval instead.
    kv_heads = a("attention.head_count_kv")
    head_dim = a("attention.key_length")
    if not head_dim and a("embedding_length") and a("attention.head_count"):
        head_dim = a("embedding_length") // a("attention.head_count")
    kv_bytes = 0
    if head_dim and kv_heads is not None:
        if isinstance(kv_heads, list):
            per_layer = kv_heads
        else:
            n = layers or 0
            interval = a("full_attention_interval")
            if isinstance(interval, int) and interval > 1:
                n //= interval
            per_layer = [kv_heads] * n
        kv_bytes = sum(2 * int(h) * head_dim * 2 for h in per_layer)

    params = mi.get("general.parameter_count")
    quant = details.get("quantization_level", "")
    info = {
        "arch": arch,
        "family": details.get("family", ""),
        "params": params,
        "params_label": _params_label(params, details.get("parameter_size", "")),
        "size_label": mi.get("general.size_label", ""),
        "quant": quant,
        "quant_bits": _quant_bits(quant),
        "moe": {"experts": experts, "used": used} if experts and used else None,
        "max_context": a("context_length"),
        "layers": layers,
        "capabilities": d.get("capabilities", []),
        "kv_bytes_per_token": kv_bytes,
    }
    _info_cache[key] = info
    return info


async def ensure_room(client: httpx.AsyncClient, model: str, num_ctx: int | None = None) -> str | None:
    """Make sure `model` can load without exhausting unified memory.

    Evicts other chat models (and, as a last resort, the SDXL pipeline)
    first. `num_ctx` adds the KV cache that context reserves up front.
    Returns a user-facing error message if it still won't fit, None when
    it's safe to proceed.
    """
    resident = next((m for m in await _loaded_models(client) if m["name"] == model), None)
    if resident and (not num_ctx or resident.get("context_length") == num_ctx):
        return None
    r = await client.get(f"{OLLAMA}/api/tags", timeout=10)
    r.raise_for_status()
    sizes = {m["name"]: m["size"] / 2**30 for m in r.json().get("models", [])}
    ctx_gb = 0.0
    if num_ctx:
        try:
            ctx_gb = (await model_info(client, model)).get("kv_bytes_per_token", 0) * num_ctx / 2**30
        except httpx.HTTPError:
            pass
    need = sizes.get(model, 0) + ctx_gb + MEMORY_HEADROOM_GB

    # a copy resident at another context is reloaded by Ollama anyway, so
    # its memory counts as free — let the eviction pass take it too
    avail = await free_memory(client, need, keep=set() if resident else {model})
    if avail < need:
        from . import imagegen

        if imagegen.is_loaded() and await asyncio.to_thread(imagegen.unload):
            for _ in range(10):
                avail = mem_available_gb()
                if avail >= need:
                    break
                await asyncio.sleep(1)
    if avail < need:
        at_ctx = f" at a {ctx_label(num_ctx)} context" if avail >= need - ctx_gb else ""
        return (
            f"Not enough unified memory to load this model safely{at_ctx} "
            f"(needs ~{need:.0f} GB free, {avail:.0f} GB available). "
            f"Something outside the demo is holding memory — try a smaller "
            f"model, or close other apps on the box."
        )
    return None


# Ollama's /api/ps `size` under-reports some models on unified memory (a
# 26 GB load can show as 2.6 GB), so local loads are measured as the drop in
# MemAvailable across the load itself. Keyed by (model, context).
_load_cost: dict[tuple[str, int], float] = {}


async def load_measured(client: httpx.AsyncClient, body: dict, url: str = OLLAMA,
                        measure: bool = True, timeout: float = 1200) -> None:
    """POST an empty /api/generate (= load `body["model"]`) and, on the local
    host, record what the load really cost. Call after any eviction so the
    delta belongs to this model alone."""
    want = (body.get("options") or {}).get("num_ctx")
    if measure:
        # Ollama swaps a copy resident at another context in place, which
        # would net the old copy out of the delta — unload it first
        resident = next((m for m in await _loaded_models(client, url)
                         if m["name"] == body["model"]), None)
        if resident and want and resident.get("context_length") != want:
            await client.post(f"{url}/api/generate",
                              json={"model": body["model"], "keep_alive": 0}, timeout=60)
            for _ in range(30):
                await asyncio.sleep(1)
                if not any(m["name"] == body["model"] for m in await _loaded_models(client, url)):
                    break
            await asyncio.sleep(1)  # the allocator hands pages back a beat later
        elif resident:
            measure = False  # no reload coming, nothing to measure
    before = mem_available_gb() if measure else 0.0
    r = await client.post(f"{url}/api/generate", json=body, timeout=timeout)
    r.raise_for_status()
    if not measure:
        return
    cost = before - mem_available_gb()
    if cost < 0.5:  # already resident (no reload) or noise — keep any real measurement
        return
    try:
        m = next((m for m in await _loaded_models(client, url) if m["name"] == body["model"]), None)
    except (httpx.HTTPError, ValueError):
        m = None
    ctx = (m or {}).get("context_length") or (body.get("options") or {}).get("num_ctx")
    if ctx:
        _load_cost[(body["model"], int(ctx))] = round(cost, 1)


async def _prepare_model(client: httpx.AsyncClient, model: str,
                         url: str = OLLAMA, guard: bool = True,
                         options: dict | None = None) -> str | None:
    """Make room for `model` and load it. Returns an error message or None.

    `guard` applies the unified-memory eviction logic — local host only;
    remote boxes manage their own memory. `options` must match the chat
    call's, or Ollama reloads the model a second time."""
    options = options or {}
    if guard:
        err = await ensure_room(client, model, options.get("num_ctx"))
        if err:
            return err
    body = {"model": model, "keep_alive": "30m"}
    if options:
        body["options"] = options
    await load_measured(client, body, url, measure=guard)
    return None


CHAT_OPTIONS = ("num_ctx", "temperature", "num_predict")

_default_ctx: list = []  # [value] once read — None is a valid cached answer


def local_default_context() -> int | None:
    """The local Ollama's default context window, from its systemd unit
    (OLLAMA_CONTEXT_LENGTH). None when unset or unreadable."""
    if not _default_ctx:
        import subprocess

        val = None
        env_val = os.environ.get("OLLAMA_CONTEXT_LENGTH", "")
        if env_val.isdigit():  # containers set it directly; there is no systemd
            val = int(env_val)
        else:
            try:
                env = subprocess.run(["systemctl", "show", "ollama", "-p", "Environment"],
                                     capture_output=True, text=True, timeout=5).stdout
                m = re.search(r"OLLAMA_CONTEXT_LENGTH=(\d+)", env)
                val = int(m.group(1)) if m else None
            except (OSError, subprocess.SubprocessError):
                pass
        _default_ctx.append(val)
    return _default_ctx[0]


async def loaded_stats(client: httpx.AsyncClient, model: str, url: str = OLLAMA,
                       local: bool = False) -> dict | None:
    """What the resident copy of `model` actually costs: context, memory, GPU
    share. `measured` says the memory figure is a real MemAvailable delta
    (local loads) rather than Ollama's own, sometimes low, estimate."""
    try:
        m = next((m for m in await _loaded_models(client, url) if m["name"] == model), None)
    except (httpx.HTTPError, ValueError):
        return None
    if not m or not m.get("size"):
        return None
    ctx = m.get("context_length")
    measured = _load_cost.get((model, ctx)) if local and ctx else None
    # the weights alone (file size on disk) — memory_gb minus this is what the
    # context window reserves up front, used or not
    try:
        r = await client.get(f"{url}/api/tags", timeout=10)
        r.raise_for_status()
        tag = next((t for t in r.json().get("models", []) if t["name"] == model), None)
        weights_gb = round(tag["size"] / 2**30, 1) if tag and tag.get("size") else None
    except (httpx.HTTPError, ValueError, KeyError):
        weights_gb = None
    return {
        "context": ctx,
        "memory_gb": measured if measured is not None else round(m["size"] / 2**30, 1),
        "measured": measured is not None,
        "weights_gb": weights_gb,
        # share of the model resident on the GPU — below 100, layers run on the
        # CPU and tok/s collapses. Unknown (None) when ps doesn't report it.
        "gpu_pct": round(100 * m["size_vram"] / m["size"]) if m.get("size_vram") is not None else None,
    }


async def stream_chat(messages: list[dict], model: str, images: list[str] | None = None,
                      think: bool | None = None, host: str = "gb10",
                      options: dict | None = None):
    """SSE generator streaming Ollama chat tokens plus a final stats event.

    `images` (base64 strings) are attached to the last user message for
    vision-capable models. `think` toggles reasoning for models that support
    it (None = the model's default); ignored for models that can't think.
    `host` is a HOSTS registry key — the memory guard applies to local
    hosts only. `options` passes Ollama runtime options (whitelisted:
    num_ctx, temperature, num_predict).
    """
    cfg = HOSTS.get(host, HOSTS["gb10"])
    url, guard = cfg["url"], cfg["local"]
    options = {k: v for k, v in (options or {}).items() if k in CHAT_OPTIONS and v is not None}
    if "num_ctx" in options:
        options["num_ctx"] = int(options["num_ctx"])
    # num_ctx is fixed at load time — a copy resident at another context
    # gets silently reloaded, so it counts as "not loaded" for narration
    want_ctx = options.get("num_ctx") or (local_default_context() if guard else None)
    if images:
        messages = [dict(m) for m in messages]
        messages[-1]["images"] = images

    payload = {"model": model, "messages": messages, "stream": True}
    if options:
        payload["options"] = options
    start = time.time()
    n_tokens = 0
    first_token_at = None
    streamed = 0  # live tokens counted so far (thinking + answer), for the savings tile
    settled = False

    async with httpx.AsyncClient(timeout=httpx.Timeout(600, connect=10)) as client:
        try:
            if think is not None and await supports_thinking(client, model, url):
                payload["think"] = think
            # A cold model can take minutes to load; a silent connection that
            # long gets killed as a network error somewhere along the path.
            # Load it first, streaming a status event every few seconds so
            # bytes keep flowing and the UI can show what's happening.
            resident = next((m for m in await _loaded_models(client, url)
                             if m["name"] == model), None)
            if not resident or (want_ctx and resident.get("context_length")
                                and resident["context_length"] != want_ctx):
                load_opts = {"num_ctx": want_ctx} if want_ctx else None
                prep = asyncio.ensure_future(_prepare_model(client, model, url, guard, load_opts))
                while True:
                    done, _ = await asyncio.wait({prep}, timeout=3)
                    elapsed = int(time.time() - start)
                    if done:
                        break
                    yield f"data: {json.dumps({'status': f'Loading the model into memory… {elapsed}s'})}\n\n"
                guard_error = prep.result()
                if guard_error:
                    yield f"event: error\ndata: {json.dumps({'error': guard_error})}\n\n"
                    return
            async with client.stream("POST", f"{url}/api/chat", json=payload) as resp:
                if resp.status_code != 200:
                    body = (await resp.aread()).decode(errors="replace")[:300]
                    try:  # unwrap Ollama's nested error envelope for a readable message
                        err = json.loads(body).get("error", body)
                        body = err.get("message", body) if isinstance(err, dict) else str(err)
                    except ValueError:
                        pass
                    yield f"event: error\ndata: {json.dumps({'error': body})}\n\n"
                    return
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    chunk = json.loads(line)
                    thinking = chunk.get("message", {}).get("thinking", "")
                    if thinking:  # reasoning models (qwen3.6 etc.) think before answering
                        streamed += 1
                        tokens.add_pending()
                        yield f"data: {json.dumps({'think': thinking})}\n\n"
                    content = chunk.get("message", {}).get("content", "")
                    if content:
                        if first_token_at is None:
                            first_token_at = time.time()
                        n_tokens += 1
                        streamed += 1
                        tokens.add_pending()
                        yield f"data: {json.dumps({'token': content})}\n\n"
                    if chunk.get("done"):
                        eval_count = chunk.get("eval_count", n_tokens)
                        eval_s = chunk.get("eval_duration", 0) / 1e9
                        tokens.settle_pending(streamed)
                        settled = True
                        prompt_eval_count = chunk.get("prompt_eval_count", 0)
                        tokens.record(in_tokens=prompt_eval_count, out_tokens=eval_count)
                        stats = {
                            "tokens": eval_count,
                            "prompt_tokens": prompt_eval_count,
                            "tok_per_sec": round(eval_count / eval_s, 1) if eval_s else None,
                            "time_to_first_token": round((first_token_at or time.time()) - start, 2),
                            "total_seconds": round(time.time() - start, 1),
                            "loaded": await loaded_stats(client, model, url, local=guard),
                        }
                        yield f"event: done\ndata: {json.dumps(stats)}\n\n"
        except httpx.HTTPError as e:
            yield f"event: error\ndata: {json.dumps({'error': f'Ollama unreachable: {e}'})}\n\n"
        finally:
            if not settled and streamed:
                # reply never finished (error / client disconnect) — the work
                # still happened, so keep what we counted as the official tally
                tokens.settle_pending(streamed)
                tokens.record(out_tokens=streamed)
