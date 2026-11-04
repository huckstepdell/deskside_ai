"""Cumulative local-inference counter — the "what this box saved you" number.

Every token generated and image rendered on this device is work that would
otherwise be metered cloud API calls. Persisted to data/token_stats.json so
the total keeps growing across restarts — the figure gets more impressive
the longer the box runs.
"""

import json
import threading
from pathlib import Path

# Reference cloud prices for the savings figure (checked 2026-08-18):
# Claude Sonnet-class API rates of $3/$15 per million input/output tokens,
# ~$0.10/M for embeddings, ~$0.04 per generated image. Adjust to taste —
# the point is a defensible, mainstream comparison, not a worst case.
CLOUD_IN_PER_M = 3.00
CLOUD_OUT_PER_M = 15.00
CLOUD_EMBED_PER_M = 0.10
CLOUD_PER_IMAGE = 0.04

STATS_FILE = Path(__file__).resolve().parent.parent / "data" / "token_stats.json"

_lock = threading.Lock()
_stats = {"in_tokens": 0, "out_tokens": 0, "embed_tokens": 0, "images": 0}
_pending_out = 0  # tokens streamed by in-flight replies, reconciled at reply end


def _load() -> None:
    try:
        saved = json.loads(STATS_FILE.read_text())
        for k in _stats:
            _stats[k] = int(saved.get(k, 0))
    except (OSError, ValueError):
        pass


def record(in_tokens: int = 0, out_tokens: int = 0, embed_tokens: int = 0, images: int = 0) -> None:
    with _lock:
        _stats["in_tokens"] += in_tokens
        _stats["out_tokens"] += out_tokens
        _stats["embed_tokens"] += embed_tokens
        _stats["images"] += images
        try:
            STATS_FILE.parent.mkdir(exist_ok=True)
            STATS_FILE.write_text(json.dumps(_stats))
        except OSError:
            pass  # counting still works in memory


def add_pending(n: int = 1) -> None:
    """Count a just-streamed token immediately, before the reply finishes."""
    global _pending_out
    with _lock:
        _pending_out += n


def settle_pending(n: int) -> None:
    """Drop n pending tokens (their official count is being record()ed)."""
    global _pending_out
    with _lock:
        _pending_out = max(0, _pending_out - n)


def summary() -> dict:
    with _lock:
        s = dict(_stats)
        out = s["out_tokens"] + _pending_out
    saved = (
        s["in_tokens"] / 1e6 * CLOUD_IN_PER_M
        + out / 1e6 * CLOUD_OUT_PER_M
        + s["embed_tokens"] / 1e6 * CLOUD_EMBED_PER_M
        + s["images"] * CLOUD_PER_IMAGE
    )
    return {
        **s,
        "out_tokens": out,
        "total_tokens": s["in_tokens"] + out + s["embed_tokens"],
        "saved_usd": round(saved, 4),
    }


_load()
