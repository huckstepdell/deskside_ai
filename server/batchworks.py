"""Batch Works — bulk AI jobs that showcase sustained local token throughput.

The pitch: batch processing is the workload companies pay cloud APIs for
(nightly ticket triage, catalog copy, log digestion). Running one here keeps
the GPU pegged for minutes and makes the savings counter climb in real time.

Items are synthetic but deterministic (index-composed, no randomness), so a
given job/count always produces the same workload — handy for repeatable
demos and benchmarks.
"""

import asyncio
import json
import time

import httpx

from . import tokens
from .chat import OLLAMA, _loaded_models, _prepare_model, ensure_room, supports_thinking

_running = False

SUPPORT_KB = """You are the AI triage assistant for the Delltech customer support desk.

COMPANY POLICIES (apply these when triaging and drafting replies):
- Returns: 30 days from delivery, free return shipping on defective items.
  Non-defective returns carry a 15% restocking fee unless the customer is a
  ProSupport subscriber.
- Warranty: Standard 1 year parts and labor. ProSupport extends to 3 years
  with next-business-day onsite service. Batteries and power adapters are
  covered for 1 year regardless of tier.
- Replacements: confirmed-defective items ship as advance replacements —
  the customer receives the new unit before returning the old one.
- Refund timing: 5-7 business days after the returned item is received.

PRIORITY DEFINITIONS:
- P1: customer completely blocked from working, or safety issue (smoke,
  swollen battery, sparking). Response within 1 hour.
- P2: major function broken with no workaround, or repeat failure on a
  replacement unit. Response same business day.
- P3: degraded experience with a workaround available. Response in 1
  business day.
- P4: questions, feature requests, cosmetic issues. Response in 2 business
  days.

TONE GUIDE: warm, direct, no corporate filler. Apologize once, concretely.
Never blame the customer. Offer the next step in the first two sentences.
Sign as "The Delltech Support Team". Escalate to a human specialist for
legal threats, executive complaints, or anything involving data loss."""

_NAMES = ["Jordan Ellis", "Priya Raman", "Miguel Santos", "Dana Whitfield", "Sam Okafor",
          "Lena Fischer", "Chris Doyle", "Aiko Tanaka", "Rob Beaumont", "Tasha Green",
          "Omar Haddad", "Beth Calloway"]
_PRODUCTS = ["Latitude 7455 laptop", "Pro Max GB10 workstation", "UltraSharp 32 monitor",
             "PowerEdge R770 server", "WD25 docking station", "Precision 5690 mobile workstation",
             "Inspiron 16 Plus", "Alienware m18 laptop", "OptiPlex 7020 desktop", "S3225QC monitor"]
_ISSUES = [
    "arrived with a cracked outer shell and the box was visibly crushed. I need this for a client presentation in three days",
    "won't power on at all since yesterday. The charge light blinks amber three times and then nothing",
    "has started making a loud grinding fan noise whenever I open more than a few browser tabs",
    "was working fine until the latest firmware update, and now the USB-C ports disconnect randomly every few minutes",
    "is my second replacement unit and it has the exact same flickering screen issue as the first two",
    "was supposed to arrive last Tuesday, but tracking has shown 'label created' for nine days now",
    "runs incredibly hot — hot enough that I can't keep it on my lap — even when it's just idling",
    "was billed twice on my credit card statement. I only ordered one and only received one",
    "keeps dropping off Wi-Fi every twenty minutes or so, and I've already replaced my router to rule that out",
    "has a battery that swelled up enough to push the trackpad out of its housing. I've stopped using it because it doesn't seem safe",
    "shipped with a US keyboard, but I ordered the German layout — the order confirmation even says QWERTZ",
    "shows dead pixels in a cluster near the center of the screen, and it's distracting for the photo editing work I bought it for",
]
_MOODS = [
    "I'm honestly pretty frustrated at this point.",
    "I love your products normally, so I'm hoping this is an easy fix.",
    "This is for my small business, so downtime really hurts.",
    "I've been a customer for over a decade and never had an issue before.",
    "Please treat this as urgent.",
    "I'm not angry, just disappointed — and I need a plan.",
    "My IT department told me to contact you directly.",
]


def _ticket(i: int) -> str:
    name = _NAMES[i % len(_NAMES)]
    product = _PRODUCTS[i % len(_PRODUCTS)]
    issue = _ISSUES[i % len(_ISSUES)]
    mood = _MOODS[i % len(_MOODS)]
    order = 48000 + i * 37
    days = 2 + (i % 9)
    return (f"From: {name}\nOrder: #{order}\n\n"
            f"Hi, my {product} {issue}. It was delivered {days} days ago. {mood} "
            f"I've already tried the basic troubleshooting on your support site and nothing changed. "
            f"What are my options here — repair, replacement, or refund? Please let me know what you "
            f"need from me to move this forward.\n\n— {name}")


_SPECS = [
    ("Latitude 7455 laptop", "14-inch 2.8K OLED, Snapdragon X Elite, 32GB RAM, 21-hour battery, 1.09kg"),
    ("Pro Max GB10 workstation", "NVIDIA GB10 Grace Blackwell superchip, 128GB unified memory, petaFLOP-class AI on a desk"),
    ("UltraSharp 32 monitor", "31.5-inch 6K IPS Black, Thunderbolt 4 with 140W power delivery, built-in 4K webcam"),
    ("PowerEdge R770 server", "2U, dual Xeon 6, 8TB DDR5, liquid-cooling ready, designed for AI inference fleets"),
    ("WD25 docking station", "Thunderbolt 5, triple 4K displays, 100GbE-ready module bay, 240W charging"),
    ("Precision 5690 mobile workstation", "16-inch, RTX 5000 Ada, ISV-certified, 64GB ECC RAM option"),
    ("Inspiron 16 Plus", "16-inch 2.5K, Core Ultra 7, 16GB RAM, all-day battery, under $1,100"),
    ("Alienware m18 laptop", "18-inch 480Hz panel, RTX 5090, Cryo-tech cooling, per-key AlienFX"),
    ("OptiPlex 7020 desktop", "1L micro form factor, Core i7, vPro manageability, 92% recycled aluminum chassis"),
    ("S3225QC monitor", "32-inch 4K QD-OLED, 120Hz, built-in spatial audio soundbar, USB-C hub"),
]
_AUDIENCES = ["busy executives", "creative professionals", "IT decision makers",
              "students and first-time buyers", "engineers and developers", "small business owners"]
_TONES = ["confident and punchy", "warm and reassuring", "technical but accessible",
          "premium and aspirational", "practical and value-focused"]


def _product_brief(i: int) -> str:
    name, specs = _SPECS[i % len(_SPECS)]
    audience = _AUDIENCES[i % len(_AUDIENCES)]
    tone = _TONES[i % len(_TONES)]
    return (f"PRODUCT: {name}\nKEY SPECS: {specs}\n"
            f"TARGET AUDIENCE: {audience}\nDESIRED TONE: {tone}\n"
            f"VARIANT: campaign draft #{i + 1}")


JOBS = {
    "tickets": {
        "label": "Support ticket triage",
        "blurb": "Classify, prioritize, and draft a reply for each inbound ticket",
        "system": SUPPORT_KB,
        "item": _ticket,
        "instruction": ("Triage the support ticket below using the company policies. Respond with exactly "
                        "three sections:\nCATEGORY: one of billing / hardware / software / shipping / safety\n"
                        "PRIORITY: P1-P4 with a one-line justification\n"
                        "DRAFT REPLY: 3-6 warm, concrete sentences following the tone guide.\n"
                        "Keep the whole response under 180 words."),
    },
    "products": {
        "label": "Product marketing copy",
        "blurb": "Write catalog descriptions from raw spec sheets",
        "system": ("You are a senior product copywriter. You turn raw spec sheets into catalog copy "
                   "that is accurate, scannable, and never uses the words 'unleash', 'elevate', or 'seamless'."),
        "item": _product_brief,
        "instruction": ("Write catalog copy for the product brief below: an 80-120 word description in the "
                        "desired tone for the target audience, followed by exactly 3 bullet-point highlights. "
                        "Ground every claim in the listed specs."),
    },
}


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj)}\n\n"


def _run_saved(run_in: int, run_out: int) -> float:
    return round(run_in / 1e6 * tokens.CLOUD_IN_PER_M + run_out / 1e6 * tokens.CLOUD_OUT_PER_M, 4)


async def run(job_key: str, count: int, model: str, reasoning: bool = False):
    """SSE generator: start → (item → tick* → item_done)* → done.

    `reasoning` toggles thinking for models that support it — off by default
    since batch throughput is the point; on turns the run into a watch-it-
    think showcase at a fraction of the speed."""
    global _running
    job = JOBS.get(job_key)
    if job is None:
        yield _sse({"type": "error", "error": f"Unknown job: {job_key}"})
        return
    if _running:
        yield _sse({"type": "error", "error": "A batch is already running — one at a time on a demo box."})
        return
    _running = True
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(600, connect=10)) as client:
            # cold model? load it first with visible heartbeats instead of
            # letting item 1 sit in silence for a minute
            if not any(m["name"] == model for m in await _loaded_models(client)):
                prep = asyncio.ensure_future(_prepare_model(client, model))
                t_load = time.time()
                while True:
                    done_set, _ = await asyncio.wait({prep}, timeout=3)
                    if done_set:
                        break
                    yield _sse({"type": "status",
                                "status": f"Loading the model into memory… {int(time.time() - t_load)}s"})
                err = prep.result()
            else:
                err = await ensure_room(client, model)
            if err:
                yield _sse({"type": "error", "error": err})
                return
            run_in = run_out = 0
            t0 = time.time()
            can_think = await supports_thinking(client, model)
            yield _sse({"type": "start", "total": count, "job": job["label"]})
            for i in range(1, count + 1):
                item_text = job["item"](i - 1)
                yield _sse({"type": "item", "index": i, "total": count, "preview": item_text})
                messages = [
                    {"role": "system", "content": job["system"]},
                    {"role": "user", "content": f"{job['instruction']}\n\n{item_text}"},
                ]
                streamed = 0
                settled = False
                content_seen = False
                buf: list[str] = []
                think_buf: list[str] = []
                last_emit = 0
                item_t0 = time.time()
                payload = {"model": model, "messages": messages, "stream": True}
                if can_think:
                    payload["think"] = reasoning
                try:
                    async with client.stream("POST", f"{OLLAMA}/api/chat", json=payload) as resp:
                        if resp.status_code != 200:
                            body = (await resp.aread()).decode(errors="replace")[:300]
                            yield _sse({"type": "error", "error": body})
                            return
                        async for line in resp.aiter_lines():
                            if not line.strip():
                                continue
                            chunk = json.loads(line)
                            think = chunk.get("message", {}).get("thinking", "")
                            if think:
                                streamed += 1
                                tokens.add_pending()
                                think_buf.append(think)
                            piece = chunk.get("message", {}).get("content", "")
                            if piece:
                                streamed += 1
                                tokens.add_pending()
                                content_seen = True
                                buf.append(piece)
                            if streamed - last_emit >= 15:
                                last_emit = streamed
                                elapsed = max(time.time() - item_t0, 0.1)
                                yield _sse({"type": "tick", "index": i,
                                            "delta": "".join(buf), "think_delta": "".join(think_buf),
                                            "phase": "writing" if content_seen else "thinking",
                                            "item_tokens": streamed,
                                            "tok_per_sec": round(streamed / elapsed, 1),
                                            "run_in": run_in, "run_out": run_out + streamed,
                                            "saved": _run_saved(run_in, run_out + streamed)})
                                buf = []
                                think_buf = []
                            if chunk.get("done"):
                                tokens.settle_pending(streamed)
                                settled = True
                                p_in = chunk.get("prompt_eval_count", 0)
                                p_out = chunk.get("eval_count", streamed)
                                tokens.record(in_tokens=p_in, out_tokens=p_out)
                                run_in += p_in
                                run_out += p_out
                finally:
                    if not settled and streamed:  # aborted mid-item — keep what ran
                        tokens.settle_pending(streamed)
                        tokens.record(out_tokens=streamed)
                yield _sse({"type": "item_done", "index": i, "total": count,
                            "delta": "".join(buf), "think_delta": "".join(think_buf),
                            "item_tokens": streamed,
                            "seconds": round(time.time() - item_t0, 1),
                            "run_in": run_in, "run_out": run_out,
                            "saved": _run_saved(run_in, run_out)})
            yield _sse({"type": "done", "items": count, "run_in": run_in, "run_out": run_out,
                        "saved": _run_saved(run_in, run_out),
                        "seconds": round(time.time() - t0, 1)})
    except httpx.HTTPError as e:
        yield _sse({"type": "error", "error": f"Ollama unreachable: {e}"})
    finally:
        _running = False
