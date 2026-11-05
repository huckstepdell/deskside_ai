"""Metrics agent for a compute node (UI/compute split).

Runs beside Ollama on the machine with the GPU and serves the same telemetry
the all-in-one dashboard samples locally, so a UI-only dashboard elsewhere can
show this node's real GPU, CPU and memory figures.

    uvicorn server.agent:app --host 0.0.0.0 --port 9101
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import StreamingResponse

from . import telemetry


@asynccontextmanager
async def lifespan(app: FastAPI):
    telemetry.start_sampler()
    yield
    await telemetry.stop_sampler()


app = FastAPI(title="Dell AI Demo metrics agent", lifespan=lifespan)


@app.get("/api/device")
async def device():
    return telemetry.device_info()


@app.get("/api/telemetry/latest")
async def latest():
    return {"watts": telemetry.latest_gpu_power(), "sample": telemetry._history[-1] if telemetry._history else None}


@app.get("/api/telemetry/stream")
async def stream():
    return StreamingResponse(telemetry.sse_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
