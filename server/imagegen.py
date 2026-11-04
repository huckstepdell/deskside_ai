"""SDXL image generation (in-process, lazy-loaded).

The pipeline (~7GB of weights, already cached in ~/.cache/huggingface) is
loaded on first use or via the explicit /api/imagegen/warmup endpoint so
server startup stays instant. Generation runs in a worker thread; per-step
progress is pushed to the client over SSE.
"""

import asyncio
import base64
import io
import json
import threading
import time

from . import tokens

_pipe = None
_pipe_lock = threading.Lock()
_gen_lock = threading.Lock()  # one generation at a time — it's a demo box
_last_used = 0.0

MODEL_ID = "stabilityai/stable-diffusion-xl-base-1.0"
IDLE_UNLOAD_S = 600  # free the ~7GB of unified memory after 10 min unused


def _load_pipe():
    """Load SDXL onto the GPU (idempotent, thread-safe)."""
    global _pipe, _last_used
    with _pipe_lock:
        if _pipe is None:
            try:
                import torch
                from diffusers import DiffusionPipeline
            except ImportError:
                raise RuntimeError(
                    "Image Studio isn't installed in this image — rebuild with "
                    "INSTALL_IMAGEGEN=1 (dashboard stack).")

            pipe = DiffusionPipeline.from_pretrained(
                MODEL_ID, torch_dtype=torch.float16, variant="fp16"
            )
            _pipe = pipe.to("cuda")
        _last_used = time.time()
    return _pipe


def unload() -> bool:
    """Drop the SDXL pipeline and return its unified memory to the pool.

    Skipped (returns False) if a generation is mid-flight."""
    global _pipe
    if not _gen_lock.acquire(blocking=False):
        return False
    try:
        with _pipe_lock:
            if _pipe is None:
                return False
            import gc

            import torch

            _pipe = None
            gc.collect()
            torch.cuda.empty_cache()
            return True
    finally:
        _gen_lock.release()


def unload_if_idle() -> bool:
    if _pipe is not None and time.time() - _last_used > IDLE_UNLOAD_S:
        return unload()
    return False


def is_loaded() -> bool:
    return _pipe is not None


def loaded_memory_gb() -> float:
    """Memory held by the SDXL pipeline (0 when not loaded)."""
    if _pipe is None:
        return 0.0
    import torch

    allocated = torch.cuda.memory_allocated() / 2**30
    return round(allocated, 1) if allocated > 0.5 else 7.0  # fallback estimate


def is_cached() -> bool:
    """Whether the SDXL weights exist locally (preflight check)."""
    from pathlib import Path

    cache = Path.home() / ".cache/huggingface/hub" / ("models--" + MODEL_ID.replace("/", "--"))
    return cache.exists()


async def warmup() -> None:
    await asyncio.to_thread(_load_pipe)


async def generate_sse(prompt: str, negative_prompt: str = "", steps: int = 30, seed: int | None = None):
    """SSE generator: status → per-step progress → final image (base64 PNG)."""
    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def emit(event: dict) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, event)

    def work() -> None:
        try:
            if not _gen_lock.acquire(blocking=False):
                emit({"type": "error", "error": "A generation is already running — wait for it to finish."})
                return
            try:
                if _pipe is None:
                    emit({"type": "status", "status": "Loading SDXL onto the GPU (first run only)…"})
                pipe = _load_pipe()
                emit({"type": "status", "status": "Generating…"})

                import torch

                generator = None
                if seed is not None:
                    generator = torch.Generator("cuda").manual_seed(seed)

                def on_step(_pipe, step, _timestep, kwargs):
                    emit({"type": "progress", "step": step + 1, "total": steps})
                    return kwargs

                result = pipe(
                    prompt=prompt,
                    negative_prompt=negative_prompt or None,
                    num_inference_steps=steps,
                    guidance_scale=7.0,
                    height=1024,
                    width=1024,
                    generator=generator,
                    callback_on_step_end=on_step,
                )
                buf = io.BytesIO()
                result.images[0].save(buf, format="PNG")
                emit({"type": "image", "png_b64": base64.b64encode(buf.getvalue()).decode()})
                tokens.record(images=1)
            finally:
                global _last_used
                _last_used = time.time()
                _gen_lock.release()
        except Exception as e:  # surface any CUDA/pipeline error to the UI
            emit({"type": "error", "error": str(e)})
        finally:
            emit({"type": "end"})

    threading.Thread(target=work, daemon=True).start()

    while True:
        event = await queue.get()
        if event["type"] == "end":
            break
        yield f"data: {json.dumps(event)}\n\n"
