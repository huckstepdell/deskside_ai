"""Box Bench — a small, transparent accuracy battery.

Every task is auto-checkable: numeric answers, exact extractions, format
compliance. No LLM judges, no vibes — a customer can read each task and
verify each grade. Runs at temperature 0 so a given model always earns the
same score, which makes the demo repeatable.

Accuracy tasks ask the model to end with "ANSWER: <value>"; format tasks are
graded on the raw response (that's the point of a format task).
"""

import json
import re
import threading
import time
from pathlib import Path

import httpx

from . import tokens
from .chat import HOSTS, OLLAMA, _loaded_models, _prepare_model, supports_thinking

RUNS_FILE = Path(__file__).resolve().parent.parent / "data" / "benchruns.json"
_runs_lock = threading.Lock()

_ANSWER_SUFFIX = " End your response with a final line in exactly this form: ANSWER: <value>"

TASKS = [
    {"id": "logic-family", "category": "logic", "label": "Family logic puzzle",
     "prompt": "Alice (a girl) has 3 brothers and 2 sisters. How many sisters does Alice's brother have?" + _ANSWER_SUFFIX,
     "check": {"type": "number", "expected": 3}},
    {"id": "logic-race", "category": "logic", "label": "Race ordering",
     "prompt": "In a race, Maya finished ahead of Noah. Noah finished ahead of Ali. Priya finished between Maya and Noah. Who finished third?" + _ANSWER_SUFFIX,
     "check": {"type": "text", "expected": "noah"}},
    {"id": "math-power", "category": "math", "label": "Power-bill arithmetic",
     "prompt": "A server rack draws a constant 4.2 kW. Electricity costs $0.14 per kWh. What does it cost to run for 30 days, in dollars, rounded to the nearest dollar?" + _ANSWER_SUFFIX,
     "check": {"type": "number", "expected": 423}},
    {"id": "math-change", "category": "math", "label": "Making change",
     "prompt": "I bought 3 items at $2.99 each and paid with a $10 bill and a $1 bill together. How much change should I get, in dollars?" + _ANSWER_SUFFIX,
     "check": {"type": "number", "expected": 2.03}},
    {"id": "extract-invoice", "category": "extraction", "label": "Invoice total",
     "prompt": "From this text, what is the total due? 'Invoice #4471. Subtotal $1,240.00, discount $124.00, shipping $35.50, tax $92.05. Total due: $1,243.55.'" + _ANSWER_SUFFIX,
     "check": {"type": "number", "expected": 1243.55}},
    {"id": "date-weekday", "category": "logic", "label": "Weekday arithmetic",
     "prompt": "Today is Wednesday, March 5. What day of the week will March 31 of the same year be?" + _ANSWER_SUFFIX,
     "check": {"type": "text", "expected": "monday"}},
    {"id": "logic-age", "category": "logic", "label": "The age trap",
     "prompt": "When I was 6, my sister was half my age. Now I am 70. How old is my sister?" + _ANSWER_SUFFIX,
     "check": {"type": "number", "expected": 67}},
    {"id": "attention-decimals", "category": "attention", "label": "Decimal comparison",
     "prompt": "Which number is larger: 9.11 or 9.9?" + _ANSWER_SUFFIX,
     "check": {"type": "number", "expected": 9.9}},
    {"id": "count-letters", "category": "attention", "label": "Letter counting",
     "prompt": "How many total occurrences of the letter 's' are there in the phrase 'session assessments systems'?" + _ANSWER_SUFFIX,
     "check": {"type": "number", "expected": 11}},
    {"id": "read-watts", "category": "comprehension", "label": "Reading comprehension",
     "prompt": "Read this sentence: 'The GB10 draws 240 watts at full load, one-third of what the previous tower workstation required.' How many watts did the previous workstation require?" + _ANSWER_SUFFIX,
     "check": {"type": "number", "expected": 720}},
    {"id": "format-json", "category": "format", "label": "Strict JSON",
     "prompt": "Return ONLY a valid JSON object with exactly two keys: \"city\" set to \"Austin\" and \"population\" set to the number 961855. No markdown fences, no commentary, nothing else.",
     "check": {"type": "json", "expected": {"city": "Austin", "population": 961855}}},
    {"id": "format-planets", "category": "format", "label": "Exactly seven lines",
     "prompt": "List the seven planets of the solar system other than Earth, one per line. Output nothing else — no numbering, no introduction.",
     "check": {"type": "planets"}},
    {"id": "attention-reverse", "category": "attention", "label": "Spell it backwards",
     "prompt": "Spell the word 'juxtaposition' backwards. Respond with only the reversed word, nothing else.",
     "check": {"type": "reversed", "expected": "noitisopatxuj"}},
    {"id": "format-sort", "category": "format", "label": "Sort and format",
     "prompt": "Sort these numbers in descending order and return them comma-separated with no spaces and no other text: 17, 4, 99, 23, 8",
     "check": {"type": "contains", "expected": "99,23,17,8,4"}},
]

_PLANETS = {"mercury", "venus", "mars", "jupiter", "saturn", "uranus", "neptune"}

# Customer-facing: what each task is really probing. Rendered on the page.
WHY = {
    "logic-family": "Relational reasoning. The model has to realize Alice counts as one of her brother's sisters — modeling the situation instead of just reusing the numbers in the question.",
    "logic-race": "Holding several ordering constraints in mind at once (ahead-of, between). Weaker models lose track of who is where.",
    "math-power": "Multi-step business arithmetic with units: kW × hours × price. One slipped step ruins the total — this is what invoice and capacity math looks like in real work.",
    "math-change": "Trivial arithmetic wrapped in a distractor: the payment is $10 AND $1. Tests carefulness, not calculation.",
    "extract-invoice": "Pulling the one correct number out of text full of plausible decoys (subtotal, tax, shipping). This is the core skill behind document processing.",
    "date-weekday": "Calendar arithmetic: 26 days is 3 weeks and 5 days. The model must compute, not vibe.",
    "logic-age": "A famous trap. 'Half my age' at age 6 means a fixed 3-year gap — models that pattern-match answer 35 instead of 67.",
    "attention-decimals": "The famous 9.11 vs 9.9 trap: string intuition says 11 beats 9; decimal magnitude says otherwise.",
    "count-letters": "Models read text as word-chunks (tokens), not letters, so counting characters is genuinely hard for them. Pure exactness.",
    "read-watts": "Reading comprehension plus one small inference: 'one-third of' means multiply by three.",
    "format-json": "Machine-to-machine discipline: can it emit data another program could consume, with zero commentary or markdown garnish.",
    "format-planets": "Instruction compliance under a counting constraint: exactly seven lines, no intro, no numbering.",
    "attention-reverse": "The tokenization blind spot: models perceive word-chunks, not letters, so reversing a word letter-by-letter is brutally hard. No model on this box passes it.",
    "format-sort": "A tiny task with a strict output contract: sorted correctly AND formatted exactly, nothing else.",
}


def grading_text(c: dict) -> str:
    """Plain-English description of the code that grades a task."""
    kind = c["type"]
    if kind == "number":
        return f"code parses the ANSWER line and checks the number equals {c['expected']}"
    if kind == "text":
        return f"code checks the ANSWER line contains “{c['expected']}”"
    if kind == "json":
        return "the raw response must parse as bare JSON and exactly match the target object"
    if kind == "planets":
        return "exactly 7 non-empty lines, together naming all 7 planets"
    if kind == "contains":
        return f"the response must contain exactly “{c['expected']}”"
    if kind == "reversed":
        return f"the response's letters must read “{c['expected']}”"
    return "code-checked"


def _answer_line(text: str) -> str | None:
    for line in reversed(text.strip().splitlines()):
        upper = line.upper()
        if "ANSWER:" in upper:
            return line[upper.rindex("ANSWER:") + 7:].strip()
    return None


def _first_number(s: str) -> float | None:
    m = re.search(r"[-+]?[\d,]*\.?\d+", s)
    if not m:
        return None
    try:
        return float(m.group().replace(",", ""))
    except ValueError:
        return None


def check(task: dict, text: str) -> tuple[bool, str]:
    """Grade a response. Returns (passed, short description of what we got)."""
    c = task["check"]
    kind = c["type"]
    if kind in ("number", "text"):
        ans = _answer_line(text)
        if ans is None:
            return False, "no ANSWER line"
        if kind == "number":
            got = _first_number(ans)
            return (got is not None and abs(got - c["expected"]) < 0.01,
                    "ANSWER: " + ans[:60])
        norm = re.sub(r"[^a-z0-9 ]", "", ans.lower()).strip()
        return c["expected"] in norm, "ANSWER: " + ans[:60]
    raw = text.strip()
    if kind == "json":
        try:
            return json.loads(raw) == c["expected"], raw[:80]
        except ValueError:
            return False, "not valid bare JSON: " + raw[:60]
    if kind == "planets":
        lines = [ln.strip().lower() for ln in raw.splitlines() if ln.strip()]
        found = {p for ln in lines for p in _PLANETS if p in ln}
        ok = len(lines) == 7 and found == _PLANETS
        return ok, f"{len(lines)} lines, {len(found)}/7 planets"
    if kind == "one_word":
        words = re.findall(r"[A-Za-z]+", raw)
        return (len(words) == 1 and words[0].lower() == c["expected"],
                raw[:40])
    if kind == "contains":
        return c["expected"] in raw.replace(" ", ""), raw[:60]
    if kind == "reversed":
        letters = re.sub(r"[^a-z]", "", raw.lower())
        return letters == c["expected"], raw[:40]
    return False, "unknown check"


# ------------------------------------------------------------------ storage

def _load_runs() -> list[dict]:
    try:
        return json.loads(RUNS_FILE.read_text())
    except (OSError, ValueError):
        return []


def save_run(entry: dict) -> None:
    with _runs_lock:
        runs = _load_runs()
        runs.append(entry)
        try:
            RUNS_FILE.parent.mkdir(exist_ok=True)
            RUNS_FILE.write_text(json.dumps(runs[-200:]))
        except OSError:
            pass


def list_runs() -> list[dict]:
    return _load_runs()


# ------------------------------------------------------------------- runner

def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj)}\n\n"


async def run(host: str, model: str, reasoning: str = "default"):
    """SSE: status → (task → task_done)* → done. Grades are deterministic
    (temperature 0); `reasoning` is default|off|on for thinking models."""
    cfg = HOSTS.get(host)
    if cfg is None:
        yield _sse({"type": "error", "error": f"Unknown host: {host}"})
        return
    url = cfg["url"]
    think = {"off": False, "on": True}.get(reasoning)
    t0 = time.time()
    results = []
    run_tokens = 0
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(600, connect=10)) as client:
            if not any(m["name"] == model for m in await _loaded_models(client, url)):
                yield _sse({"type": "status", "status": "Loading the model into memory…"})
                err = await _prepare_model(client, model, url, guard=cfg["local"])
                if err:
                    yield _sse({"type": "error", "error": err})
                    return
            use_think = think is not None and await supports_thinking(client, model, url)
            for i, task in enumerate(TASKS, 1):
                yield _sse({"type": "task", "index": i, "total": len(TASKS),
                            "id": task["id"], "label": task["label"],
                            "category": task["category"]})
                payload = {"model": model, "stream": True,
                           "messages": [{"role": "user", "content": task["prompt"]}],
                           "options": {"temperature": 0}}
                if use_think:
                    payload["think"] = think
                streamed = 0
                settled = False
                content: list[str] = []
                task_t0 = time.time()
                try:
                    async with client.stream("POST", f"{url}/api/chat", json=payload) as resp:
                        if resp.status_code != 200:
                            body = (await resp.aread()).decode(errors="replace")[:200]
                            yield _sse({"type": "error", "error": body})
                            return
                        async for line in resp.aiter_lines():
                            if not line.strip():
                                continue
                            chunk = json.loads(line)
                            if chunk.get("message", {}).get("thinking"):
                                streamed += 1
                                tokens.add_pending()
                            piece = chunk.get("message", {}).get("content", "")
                            if piece:
                                streamed += 1
                                tokens.add_pending()
                                content.append(piece)
                            if chunk.get("done"):
                                tokens.settle_pending(streamed)
                                settled = True
                                tokens.record(in_tokens=chunk.get("prompt_eval_count", 0),
                                              out_tokens=chunk.get("eval_count", streamed))
                finally:
                    if not settled and streamed:
                        tokens.settle_pending(streamed)
                        tokens.record(out_tokens=streamed)
                run_tokens += streamed
                passed, got = check(task, "".join(content))
                results.append(passed)
                yield _sse({"type": "task_done", "index": i, "total": len(TASKS),
                            "id": task["id"], "passed": passed, "got": got,
                            "seconds": round(time.time() - task_t0, 1),
                            "score": sum(results)})
        entry = {"host": host, "model": model, "reasoning": reasoning,
                 "score": sum(results), "total": len(TASKS),
                 "seconds": round(time.time() - t0, 1), "tokens": run_tokens,
                 "at": time.strftime("%Y-%m-%d %H:%M")}
        save_run(entry)
        yield _sse({"type": "done", **entry})
    except httpx.HTTPError as e:
        yield _sse({"type": "error", "error": f"Inference host unreachable: {e}"})
