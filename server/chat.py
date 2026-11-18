"""Streaming chat proxy and helpers for the vLLM OpenAI-compatible service."""

import asyncio
import json
import os
import re
import time
from pathlib import Path

import httpx

from . import telemetry, tokens
from .config import CATALOG as MODEL_CATALOG
from .config import EMBED_URL, HOSTS, LOCAL_VLLM as VLLM, role

# Compatibility alias for modules that still import OLLAMA
OLLAMA = VLLM

MEMORY_HEADROOM_GB = 10

VISION_MODEL = role("vision")
EMBED_MODEL = role("embed") or "BAAI/bge-small-en-v1.5"

CHAT_OPTIONS = ("num_ctx", "temperature", "num_predict", "seed")

_THINKING_PATTERNS = ("qwen3", "deepseek-r1", "qwq", "nemotron", "muse-glimmer")


class VLLMError(RuntimeError):
    """Non-200 response from vLLM with a human-readable message."""


def _unwrap_error(body: str) -> str:
    try:
        err = json.loads(body).get("error", body)
        return err.get("message", body) if isinstance(err, dict) else str(err)
    except ValueError:
        return body


async def list_models() -> list[dict]:
    """Models currently served by vLLM, decorated with demo-friendly labels."""
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{VLLM}/v1/models", timeout=10)
        r.raise_for_status()
    served = {m["id"]: m for m in r.json().get("data", [])}
    out = []
    for name, meta in MODEL_CATALOG.items():
        if name in served:
            out.append({
                "name": name,
                "label": meta["label"],
                "blurb": meta["blurb"],
                "traits": meta.get("traits", ""),
                "size_gb": meta.get("size_gb", 0),
                "max_model_len": served[name].get("max_model_len"),
            })
    for name, m in sorted(served.items()):
        if name in MODEL_CATALOG or name == EMBED_MODEL:
            continue
        out.append({
            "name": name,
            "label": name.split("/")[-1],
            "blurb": "",
            "traits": "",
            "size_gb": 0,
            "max_model_len": m.get("max_model_len"),
        })
    return out


async def supports_thinking(client: httpx.AsyncClient, model: str, url: str = VLLM) -> bool:
    """Whether `model` supports reasoning/thinking toggles."""
    if os.environ.get("VLLM_REASONING_PARSER"):
        return True
    low = model.lower()
    return any(p in low for p in _THINKING_PATTERNS)


def mem_available_gb() -> float:
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / 2**20
    except OSError:
        pass
    return 0.0


async def _loaded_models(client: httpx.AsyncClient, url: str = VLLM) -> list[dict]:
    r = await client.get(f"{url}/v1/models", timeout=10)
    r.raise_for_status()
    return [
        {"name": m["id"], "context_length": m.get("max_model_len")}
        for m in r.json().get("data", [])
    ]


def ctx_label(n: int | None) -> str:
    """4096 → 4k, 131072 → 128k, 262144 → 262k, 1048576 → 1M."""
    if not n:
        return ""
    if n >= 2**20 and n % 2**20 == 0:
        return f"{n // 2**20}M"
    return f"{round(n / (1000 if n > 131072 else 1024))}k"


async def model_info(client: httpx.AsyncClient, model: str, url: str = VLLM) -> dict:
    """Compact spec for `model` read from vLLM's /v1/models."""
    r = await client.get(f"{url}/v1/models", timeout=10)
    r.raise_for_status()
    entry = next((m for m in r.json().get("data", []) if m["id"] == model), {})
    return {
        "arch": "",
        "family": "",
        "params": None,
        "params_label": "",
        "size_label": "",
        "quant": "",
        "quant_bits": None,
        "moe": None,
        "max_context": entry.get("max_model_len"),
        "layers": None,
        "capabilities": ["completion", "tools"],
        "kv_bytes_per_token": 0,
    }


async def ensure_room(client: httpx.AsyncClient, model: str, num_ctx: int | None = None) -> str | None:
    """vLLM pre-allocates its KV cache and weights at startup, so no per-request
    eviction is needed."""
    return None


async def _prepare_model(client: httpx.AsyncClient, model: str,
                         url: str = VLLM, guard: bool = True,
                         options: dict | None = None) -> str | None:
    return None


def local_default_context() -> int | None:
    """The local vLLM's configured max model length (VLLM_MAX_MODEL_LEN)."""
    val = os.environ.get("VLLM_MAX_MODEL_LEN") or os.environ.get("OLLAMA_CONTEXT_LENGTH") or ""
    return int(val) if val.isdigit() else None


async def loaded_stats(client: httpx.AsyncClient, model: str, url: str = VLLM,
                       local: bool = False) -> dict | None:
    """Context window and VRAM usage for the currently served vLLM model."""
    try:
        m = next((m for m in await _loaded_models(client, url) if m["name"] == model), None)
    except (httpx.HTTPError, ValueError):
        return None
    if not m:
        return None
    vram = telemetry.vram_gb() if local else None
    return {
        "context": m.get("context_length") or (local_default_context() if local else None),
        "memory_gb": vram[0] if vram else None,
        "measured": False,
        "weights_gb": None,
        "gpu_pct": 100,
    }


def format_messages(messages: list[dict], images: list[str] | None = None) -> list[dict]:
    """Convert Ollama-style `images` fields into OpenAI `image_url` content parts."""
    out = []
    for idx, m in enumerate(messages):
        msg = dict(m)
        imgs = list(msg.pop("images", None) or [])
        if images and idx == len(messages) - 1:
            imgs.extend(images)
        if imgs:
            content = msg.get("content", "")
            parts = []
            if isinstance(content, str):
                if content:
                    parts.append({"type": "text", "text": content})
            elif isinstance(content, list):
                parts.extend(content)
            for b64 in imgs:
                url = b64 if b64.startswith("data:") else f"data:image/jpeg;base64,{b64}"
                parts.append({"type": "image_url", "image_url": {"url": url}})
            msg["content"] = parts
        out.append(msg)
    return out


def build_chat_payload(
    model: str,
    messages: list[dict],
    stream: bool = True,
    options: dict | None = None,
    think: bool | None = None,
    schema: dict | None = None,
    tools: list[dict] | None = None,
) -> dict:
    """Build an OpenAI-compatible /v1/chat/completions payload for vLLM."""
    payload: dict = {"model": model, "messages": messages, "stream": stream}
    if stream:
        payload["stream_options"] = {"include_usage": True}
    if options:
        if options.get("temperature") is not None:
            payload["temperature"] = options["temperature"]
        if options.get("num_predict") is not None:
            payload["max_tokens"] = int(options["num_predict"])
        if options.get("seed") is not None:
            payload["seed"] = int(options["seed"])
    if schema is not None:
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "response", "schema": schema, "strict": True},
        }
    if think is not None:
        payload["chat_template_kwargs"] = {"enable_thinking": bool(think)}
    if tools:
        payload["tools"] = tools
    return payload


async def iter_vllm_chat(client: httpx.AsyncClient, url: str, payload: dict):
    """Stream /v1/chat/completions from vLLM, yielding normalized chunk dicts.

    Raises VLLMError on non-200 HTTP responses.
    """
    async with client.stream("POST", f"{url}/v1/chat/completions", json=payload) as resp:
        if resp.status_code != 200:
            body = (await resp.aread()).decode(errors="replace")[:300]
            raise VLLMError(_unwrap_error(body))
        async for line in resp.aiter_lines():
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if not data or data == "[DONE]":
                continue
            chunk = json.loads(data)
            usage = chunk.get("usage")
            choices = chunk.get("choices") or []
            if not choices:
                if usage:
                    yield {"content": "", "thinking": "", "tool_calls": [],
                           "finish_reason": None, "usage": usage}
                continue
            choice = choices[0]
            delta = choice.get("delta") or {}
            yield {
                "content": delta.get("content") or "",
                "thinking": delta.get("reasoning_content") or delta.get("reasoning") or "",
                "tool_calls": delta.get("tool_calls") or [],
                "finish_reason": choice.get("finish_reason"),
                "usage": usage,
            }


async def stream_chat(messages: list[dict], model: str, images: list[str] | None = None,
                      think: bool | None = None, host: str = "gb10",
                      options: dict | None = None):
    """SSE generator streaming vLLM chat tokens plus a final stats event."""
    cfg = HOSTS.get(host, HOSTS["gb10"])
    url, guard = cfg["url"], cfg["local"]
    options = {k: v for k, v in (options or {}).items() if k in CHAT_OPTIONS and v is not None}

    formatted = format_messages(messages, images)
    start = time.time()
    n_tokens = 0
    first_token_at = None
    streamed = 0
    settled = False

    async with httpx.AsyncClient(timeout=httpx.Timeout(600, connect=10)) as client:
        try:
            use_think = think if (think is not None and await supports_thinking(client, model, url)) else None
            payload = build_chat_payload(model, formatted, stream=True, options=options, think=use_think)

            prompt_tokens = 0
            completion_tokens = 0
            async for chunk in iter_vllm_chat(client, url, payload):
                if chunk["thinking"]:
                    streamed += 1
                    tokens.add_pending()
                    yield f"data: {json.dumps({'think': chunk['thinking']})}\n\n"
                if chunk["content"]:
                    if first_token_at is None:
                        first_token_at = time.time()
                    n_tokens += 1
                    streamed += 1
                    tokens.add_pending()
                    yield f"data: {json.dumps({'token': chunk['content']})}\n\n"
                if chunk["usage"]:
                    prompt_tokens = chunk["usage"].get("prompt_tokens", prompt_tokens)
                    completion_tokens = chunk["usage"].get("completion_tokens", completion_tokens)

            eval_count = completion_tokens or n_tokens
            eval_s = max(time.time() - (first_token_at or start), 0.001)
            tokens.settle_pending(streamed)
            settled = True
            tokens.record(in_tokens=prompt_tokens, out_tokens=eval_count)
            stats = {
                "tokens": eval_count,
                "prompt_tokens": prompt_tokens,
                "tok_per_sec": round(eval_count / eval_s, 1) if eval_count else None,
                "time_to_first_token": round((first_token_at or time.time()) - start, 2),
                "total_seconds": round(time.time() - start, 1),
                "loaded": await loaded_stats(client, model, url, local=guard),
            }
            yield f"event: done\ndata: {json.dumps(stats)}\n\n"
        except VLLMError as e:
            yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"
        except httpx.HTTPError as e:
            yield f"event: error\ndata: {json.dumps({'error': f'vLLM unreachable: {e}'})}\n\n"
        finally:
            if not settled and streamed:
                tokens.settle_pending(streamed)
                tokens.record(out_tokens=streamed)
