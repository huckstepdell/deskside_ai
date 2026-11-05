"""Live silicon telemetry for the GB10.

Samples GPU (via NVML), unified memory (/proc/meminfo — the GB10's 128GB is
shared between CPU and GPU, so system memory IS the GPU memory story) and
CPU (psutil) on a background task, and fans the readings out to any number
of SSE subscribers.
"""

import asyncio
import json
import os
import time
from contextlib import suppress

import psutil

try:
    import pynvml

    pynvml.nvmlInit()
    _NVML_HANDLE = pynvml.nvmlDeviceGetHandleByIndex(0)
    NVML_OK = True
except Exception:  # pragma: no cover - dev machines without a GPU
    _NVML_HANDLE = None
    NVML_OK = False

SAMPLE_INTERVAL = 0.5  # seconds → 2Hz
HISTORY_SECONDS = 120  # how much history a new client gets

_history: list[dict] = []
_subscribers: set[asyncio.Queue] = set()
_sampler_task: asyncio.Task | None = None


def _gpu_sample() -> dict:
    if not NVML_OK:
        return {"gpu_util": 0, "gpu_temp": 0, "gpu_power": 0.0, "gpu_clock": 0}
    util = temp = clock = 0
    power = 0.0
    vram_used = vram_total = None  # discrete GPUs; NVML reports N/A on unified-memory boxes
    with suppress(pynvml.NVMLError):
        mi = pynvml.nvmlDeviceGetMemoryInfo(_NVML_HANDLE)
        vram_used, vram_total = round(mi.used / 2**30, 2), round(mi.total / 2**30, 2)
    with suppress(pynvml.NVMLError):
        util = pynvml.nvmlDeviceGetUtilizationRates(_NVML_HANDLE).gpu
    with suppress(pynvml.NVMLError):
        temp = pynvml.nvmlDeviceGetTemperature(_NVML_HANDLE, pynvml.NVML_TEMPERATURE_GPU)
    with suppress(pynvml.NVMLError):
        power = pynvml.nvmlDeviceGetPowerUsage(_NVML_HANDLE) / 1000.0  # mW → W
    with suppress(pynvml.NVMLError):
        clock = pynvml.nvmlDeviceGetClockInfo(_NVML_HANDLE, pynvml.NVML_CLOCK_SM)
    return {"gpu_util": util, "gpu_temp": temp, "gpu_power": round(power, 1), "gpu_clock": clock,
            "vram_used_gb": vram_used, "vram_total_gb": vram_total}


def vram_gb() -> tuple[float, float] | None:
    """(used, total) dedicated GPU memory in GB; None when NVML can't report it (e.g. unified memory)."""
    if not NVML_OK:
        return None
    sample = _gpu_sample()
    if sample.get("vram_used_gb") is None:
        return None
    return sample["vram_used_gb"], sample["vram_total_gb"]


def _sample() -> dict:
    mem = psutil.virtual_memory()
    sample = {
        "t": time.time(),
        "cpu_util": psutil.cpu_percent(interval=None),
        "cpu_per_core": psutil.cpu_percent(interval=None, percpu=True),
        "mem_used_gb": round((mem.total - mem.available) / 2**30, 2),
        "mem_total_gb": round(mem.total / 2**30, 2),
        "mem_pct": mem.percent,
    }
    sample.update(_gpu_sample())
    return sample


async def _sampler_loop() -> None:
    max_len = int(HISTORY_SECONDS / SAMPLE_INTERVAL)
    while True:
        sample = await asyncio.to_thread(_sample)
        _history.append(sample)
        del _history[:-max_len]
        for q in list(_subscribers):
            if q.full():  # slow client — drop oldest so we never block
                with suppress(asyncio.QueueEmpty):
                    q.get_nowait()
            q.put_nowait(sample)
        await asyncio.sleep(SAMPLE_INTERVAL)


def latest_gpu_power() -> float | None:
    """Most recent GPU power sample in watts (None before the first sample)."""
    return _history[-1]["gpu_power"] if _history else None


def start_sampler() -> None:
    global _sampler_task
    if _sampler_task is None:
        psutil.cpu_percent(interval=None)  # prime the delta-based counters
        _sampler_task = asyncio.get_event_loop().create_task(_sampler_loop())


async def stop_sampler() -> None:
    global _sampler_task
    if _sampler_task is not None:
        _sampler_task.cancel()
        with suppress(asyncio.CancelledError):
            await _sampler_task
        _sampler_task = None


def device_info() -> dict:
    """Static hardware identity for the Overview page."""
    info = {
        "device": os.environ.get("DEVICE_NAME") or "Dell Pro Max GB10",
        "superchip": "NVIDIA GB10 Grace Blackwell",
        "cpu": "20-core Arm (10× Cortex-X925 + 10× Cortex-A725)",
        "memory": f"{round(psutil.virtual_memory().total / 2**30)} GB unified LPDDR5x (shared CPU+GPU)",
        "gpu": "NVIDIA Blackwell GPU",
        "cuda": None,
        "driver": None,
    }
    if os.environ.get("DEVICE_NAME"):  # not a GB10 — describe the machine generically
        info["superchip"] = info["gpu"] = "NVIDIA GPU"
        info["cpu"] = f"{psutil.cpu_count(logical=True)} threads"
        info["memory"] = f"{round(psutil.virtual_memory().total / 2**30)} GB system memory"
    if NVML_OK:
        with suppress(pynvml.NVMLError):
            info["gpu"] = pynvml.nvmlDeviceGetName(_NVML_HANDLE)
        vram = vram_gb()
        if vram and os.environ.get("DEVICE_NAME"):
            info["memory"] = f"{round(vram[1])} GB GPU · {round(psutil.virtual_memory().total / 2**30)} GB system"
        with suppress(pynvml.NVMLError):
            info["driver"] = pynvml.nvmlSystemGetDriverVersion()
        with suppress(pynvml.NVMLError):
            v = pynvml.nvmlSystemGetCudaDriverVersion_v2()
            info["cuda"] = f"{v // 1000}.{(v % 1000) // 10}"
    return info


async def sse_stream():
    """SSE generator: replays recent history, then streams live samples."""
    queue: asyncio.Queue = asyncio.Queue(maxsize=32)
    _subscribers.add(queue)
    try:
        yield f"event: history\ndata: {json.dumps(_history)}\n\n"
        while True:
            sample = await queue.get()
            yield f"data: {json.dumps(sample)}\n\n"
    finally:
        _subscribers.discard(queue)
