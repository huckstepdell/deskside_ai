"""Agent Works — generative AI vs agentic AI, on the same task, side by side.

The rest of this dashboard demos *generative* AI: one prompt in, one answer
out, a few thousand tokens. That is not what people mean when they say
"agentic". An agent runs a loop — it decides what to look at, calls tools,
reads the results, and keeps going until the job is actually done.

Two things fall out of that loop, and both are the pitch for a box like this:

1. The answer is grounded. It read the actual files, so it cites real numbers
   instead of describing what an audit *would* look like.
2. The token cost explodes. Every iteration resends the entire conversation
   so far, so a ten-step run bills the growing context ten times over. That
   is why agentic workloads cost 10-50x what a chat turn costs, and why the
   bill scales with how useful the agent is.

Run the same task both ways and the contrast makes the argument by itself.

Tools are deliberately read-only and confined to agent-workspace/: this runs
in front of customers, so nothing an agent decides to do can surprise the
presenter.
"""

import ast
import asyncio
import json
import operator
import re
import time
from pathlib import Path

import httpx

from . import tokens
from .chat import OLLAMA, _loaded_models, _prepare_model, ensure_room, supports_thinking
from .config import ROOT, role

WORKSPACE = ROOT / "agent-workspace"

MAX_STEPS = 20          # hard stop; a well-behaved run finishes in 8-14
MAX_RESULT_CHARS = 6000  # per tool result, keeps one bad read from eating context

_running = False


# ---------------------------------------------------------------- the tasks

TASKS = {
    "expense-audit": {
        "label": "Expense audit",
        "folder": "expense-audit",
        "blurb": "Audit six expense reports against the travel policy",
        "domain": "Finance",
        "goal": (
            "Audit Meridian Logistics' six most recent expense reports against the "
            "company travel policy. For each violation, name the report, the line "
            "item, the policy rule it breaks, and the dollar amount that should not "
            "be reimbursed. Compare the reports against each other as well as against "
            "the policy. Finish with the total unreimbursable amount. Be precise with "
            "numbers."
        ),
        "agent_hint": (
            "Read the policy first, then take the reports one at a time, recording "
            "each violation before you move on to the next report."
        ),
        "finding_desc": ("Record one confirmed policy violation. Call it once per "
                         "violation, as soon as you find it, and make all of a report's "
                         "calls together. Never record a line that complies with the "
                         "policy. The running total is computed for you."),
        "finding_subject": "Which report and line, e.g. 'ER-4471 line 2'",
        "finding_amount": "The dollar amount that should not be reimbursed",
        "tally": "sum",
        "tally_label": "Total unreimbursable",
        "planted": (
            "Findings only reachable by reading several files: a $310/night Austin "
            "hotel (over the $250 cap, and Austin is not an exception city), wine on "
            "a client dinner, a business-class ticket on a 3h50m flight, a $1,850 "
            "conference fee with only verbal pre-approval, lost receipts over $25, "
            "and the same $402 client dinner claimed twice — on two different "
            "reports, so a single-document reader cannot see it."
        ),
    },
    "incident-review": {
        "label": "Support escalation",
        "folder": "incident-review",
        "blurb": "Find the systemic cause behind a batch of support tickets",
        "domain": "Support / IT",
        "goal": (
            "Seven support tickets are open. Work out whether any of them share a "
            "single systemic root cause. Use the fleet inventory, the firmware "
            "changelog, and the published known-issues list to support your "
            "conclusion. State which tickets are part of the pattern, which are "
            "not and why, the exact configuration the affected machines share, the "
            "firmware change that plausibly caused it, and — counting only machines "
            "that have NOT filed one of these tickets — how many others in the fleet "
            "carry the same at-risk configuration."
        ),
        "agent_hint": (
            "Use count_matching for the fleet count rather than eyeballing the "
            "inventory rows, then subtract the machines that already have tickets."
        ),
        "finding_desc": ("Record your verdict on ONE ticket — never group several tickets "
                         "into one call. Call this once per ticket, as soon as you have "
                         "decided, so the triaged count is right."),
        "finding_subject": "The ticket ID and asset tag, e.g. 'TK-88104 (DT-1002)'",
        "finding_amount": "Leave this out — these findings have no dollar value",
        "tally": "count",
        "tally_label": "Tickets triaged",
        "planted": (
            "The dropout tickets are all firmware 2.4.1 on a WD25 dock, and 2.4.1's "
            "changelog rewrote USB-C power-delivery renegotiation — which matches "
            "every 'it happens when someone plugs something else in' report. Three "
            "tickets are decoys that match published known issues. Eleven fleet "
            "units carry the 2.4.1 + WD25 combination; four have filed tickets, so "
            "seven are silently exposed. No single file contains that answer."
        ),
    },
}


def task_list() -> list[dict]:
    return [
        {"key": k, "label": t["label"], "blurb": t["blurb"],
         "domain": t["domain"], "goal": t["goal"], "planted": t["planted"],
         "files": _file_count(t["folder"])}
        for k, t in TASKS.items()
    ]


def _file_count(folder: str) -> int:
    root = WORKSPACE / folder
    return sum(1 for p in root.rglob("*") if p.is_file()) if root.is_dir() else 0


# ---------------------------------------------------------------- the tools

def tool_specs(task: dict) -> list[dict]:
    """The tool list for one task. record_finding is described in that task's
    own vocabulary — a generic 'log something interesting' tool gets used
    inconsistently, which defeats the point of structured findings."""
    return TOOL_SPECS + [{
        "type": "function",
        "function": {
            "name": "record_finding",
            "description": task["finding_desc"],
            "parameters": {
                "type": "object",
                "properties": {
                    "subject": {"type": "string", "description": task["finding_subject"]},
                    "issue": {"type": "string", "description": "Short label for what you found (under 8 words)"},
                    "detail": {"type": "string", "description": "One sentence citing the specific evidence"},
                    "amount": {"type": "number", "description": task["finding_amount"]},
                },
                "required": ["subject", "issue", "detail"],
            },
        },
    }]


TOOL_SPECS = [
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": ("List every file available in the workspace, with its size. "
                            "Call this first to see what you have to work with."),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": ("Read the full contents of one workspace file. Use the exact "
                            "path returned by list_files."),
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Workspace-relative file path"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": ("Case-insensitive search for a string across every workspace "
                            "file. Returns matching lines with their file and line number. "
                            "Good for finding which files mention a term."),
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "Text to search for"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "count_matching",
            "description": ("Count the lines that contain ALL of the given terms, across "
                            "every workspace file. Use this whenever the answer is 'how "
                            "many' — counting rows by eye is how you get it wrong."),
            "parameters": {
                "type": "object",
                "properties": {
                    "terms": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Every term a line must contain, e.g. ['2.4.1', 'WD25']",
                    },
                },
                "required": ["terms"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate",
            "description": ("Evaluate an arithmetic expression, e.g. '310 - 250' or "
                            "'(402 / 2) + 78'. Use this instead of doing sums in your head."),
            "parameters": {
                "type": "object",
                "properties": {"expression": {"type": "string", "description": "Arithmetic expression"}},
                "required": ["expression"],
            },
        },
    },
]

_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.Pow: operator.pow, ast.Mod: operator.mod,
    ast.USub: operator.neg, ast.UAdd: operator.pos,
}


def _safe_eval(node) -> float:
    """Arithmetic only — no names, calls, or attribute access."""
    if isinstance(node, ast.Expression):
        return _safe_eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_safe_eval(node.left), _safe_eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_safe_eval(node.operand))
    raise ValueError("only numbers and + - * / % ** are allowed")


def _resolve(folder: str, rel: str) -> Path:
    """Workspace-relative path → absolute, refusing anything outside it."""
    root = (WORKSPACE / folder).resolve()
    target = (root / rel.lstrip("/")).resolve()
    if not target.is_relative_to(root):
        raise ValueError("path is outside the workspace")
    return target


def _workspace_files(folder: str) -> list[Path]:
    root = WORKSPACE / folder
    return sorted(p for p in root.rglob("*") if p.is_file())


def run_tool(folder: str, name: str, args: dict, findings: list | None = None,
             needs_amount: bool = False) -> tuple[str, str]:
    """Execute one tool call. Returns (result_text, one_line_summary)."""
    root = WORKSPACE / folder
    try:
        if name == "record_finding":
            subject = str(args.get("subject", "")).strip()
            issue = str(args.get("issue", "")).strip()
            if not subject or not issue:
                return "Error: a finding needs both a subject and an issue.", "incomplete finding"
            amount = args.get("amount")
            try:
                amount = float(amount) if amount is not None else None
            except (TypeError, ValueError):
                amount = None
            # On a money task, a finding worth $0.00 is a compliant line the
            # model logged anyway. Rejecting it keeps the tally meaningful and
            # stops a run burning its step budget on non-findings.
            if needs_amount and not amount:
                return ("Not recorded: this has no unreimbursable amount, so it is not a "
                        "violation. Only record lines that actually break a rule, with the "
                        "dollar amount that should not be reimbursed."), "not a violation"
            entry = {"subject": subject, "issue": issue,
                     "detail": str(args.get("detail", "")).strip(), "amount": amount}
            findings = findings if findings is not None else []
            findings.append(entry)
            money = f" ({amount:,.2f})" if amount is not None else ""
            return (f"Recorded finding #{len(findings)}: {subject} — {issue}{money}. "
                    f"Totals are computed for you; keep going."), f"#{len(findings)} {subject}"

        if name == "list_files":
            files = _workspace_files(folder)
            listing = "\n".join(
                f"{p.relative_to(root)}  ({p.stat().st_size} bytes)" for p in files)
            return listing or "(workspace is empty)", f"{len(files)} files"

        if name == "read_file":
            rel = str(args.get("path", "")).strip()
            if not rel:
                return "Error: no path given.", "no path given"
            path = _resolve(folder, rel)
            if not path.is_file():
                names = ", ".join(str(p.relative_to(root)) for p in _workspace_files(folder))
                return f"Error: no such file '{rel}'. Available files: {names}", f"{rel} not found"
            text = path.read_text(errors="replace")
            return text[:MAX_RESULT_CHARS], f"{rel} · {len(text)} chars"

        if name == "search_files":
            query = str(args.get("query", "")).strip()
            if not query:
                return "Error: no query given.", "no query given"
            # Models reach for regex here out of habit; this is a plain
            # substring search, so retry once with the escapes stripped
            # rather than handing back a misleading "no matches".
            for term in (query, query.replace("\\", "")):
                hits = []
                for p in _workspace_files(folder):
                    for n, line in enumerate(p.read_text(errors="replace").splitlines(), 1):
                        if term.lower() in line.lower():
                            hits.append(f"{p.relative_to(root)}:{n}: {line.strip()[:200]}")
                if hits:
                    break
            body = "\n".join(hits[:80]) or (
                f"No matches for '{query}'. This is a plain substring search, "
                f"not a regular expression — try a simpler literal term.")
            return body, f"'{query}' · {len(hits)} matches"

        if name == "count_matching":
            terms = args.get("terms") or []
            if isinstance(terms, str):
                terms = [terms]
            terms = [str(t).strip() for t in terms if str(t).strip()]
            if not terms:
                return "Error: give at least one term to match.", "no terms given"
            hits = []
            for p in _workspace_files(folder):
                for line in p.read_text(errors="replace").splitlines():
                    low = line.lower()
                    if all(t.lower() in low for t in terms):
                        hits.append(f"{p.relative_to(root)}: {line.strip()[:160]}")
            listing = "\n".join(hits[:60])
            joined = " AND ".join(terms)
            return (f"{len(hits)} lines contain {joined}:\n{listing}",
                    f"{joined} · {len(hits)} lines")

        if name == "calculate":
            expr = str(args.get("expression", "")).strip()
            if not any(op in expr for op in "+-*/%"):
                return ("Error: nothing to compute — calculate is for real arithmetic "
                        "like '310 - 250'. If you have no calculation to do, move on to "
                        "the next tool or write your final report."), "no-op skipped"
            value = _safe_eval(ast.parse(expr, mode="eval"))
            out = f"{value:,.2f}" if isinstance(value, float) else f"{value:,}"
            return f"{expr} = {out}", f"{expr} = {out}"

        return f"Error: unknown tool '{name}'.", f"unknown tool {name}"
    except Exception as e:  # a bad tool call is data for the model, not a crash
        return f"Error: {e}", f"error: {e}"


# ------------------------------------------------------------- the prompting

AGENT_SYSTEM = """You are an analyst agent with direct access to a workspace of real files.

You work in a loop: call a tool, read the result, decide what to do next. Keep
going until you have actually answered the question.

RULES
- Never answer from assumption. Every factual claim must come from a file you
  have read in this session.
- Start by listing the files, then read the ones that matter. Read all of them
  if the task requires it — reading is cheap, being wrong is not.
- Work one item at a time. Read an item, record what you found in it, then move
  to the next. Do not read everything and analyse at the end; that is how items
  get missed.
- Do not write out your analysis as prose. A finding that isn't recorded with
  record_finding does not exist.
- Use the calculate tool for real arithmetic rather than doing sums in your
  head. Never call it with a placeholder like "0" — if there is nothing to
  compute, move on.
- Never repeat a tool call you have already made; you will get the same answer
  and waste a step.
- Cross-reference. The most important findings usually require combining two
  or more files; a fact that is obvious from a single file is rarely the point.
- Do not write commentary between tool calls. No analysis, no narration, no
  summaries — just call the next tool. At most one short sentence if it is
  genuinely needed. You will be asked for the full report at the end, and
  anything you write before then is wasted work.
- Record every finding with record_finding the moment you establish it. Make
  all of an item's calls in the same turn — if a report has three violations,
  that is three record_finding calls together, not three separate turns.
- Record only actual findings. A line that complies with the rules is not a
  finding and must not be recorded. Totals are computed from these records, so
  padding them corrupts the answer.
- When you have worked through everything the task covers and recorded each
  finding, stop calling tools. That is the signal that you are ready to report."""

REPORT_TEMPLATE = """Investigation complete. Here are the findings you recorded, with the
totals computed for you:

{findings}

Write the final report now, for a decision-maker who has not seen any of the
files and will not see the table above.

FORMAT
- Open with the bottom line: what you found and what it means, in one short
  paragraph.
- Then any conclusion the table alone does not carry — the root cause, the
  pattern, what was ruled out and why, what should happen next.
- Close with **{tally_label}: {tally_value}** on its own line.

RULES
- Under 350 words. No preamble, no restating the task, no describing your
  process.
- Do not re-list every finding; the table is already in front of the reader.
- Use the computed figures above exactly as given. Do no fresh arithmetic.
- Do not reconsider anything. You already did the work."""

GENERATIVE_SYSTEM = """You are a helpful AI assistant. Answer the user's request as well as you
can in a single response. Be concrete and useful."""

STUFFED_SYSTEM = """You are a careful analyst. Everything you need is in the documents below —
work only from them, cite the specific records behind every claim, and be
precise with numbers. Lead with your conclusion, then the findings as a table,
then the total on its own line. Under 400 words."""


def stuffed_prompt(task: dict) -> tuple[str, int]:
    """Every workspace file inlined into one prompt — the comparison a customer
    will reasonably ask for ("just paste it all in"). Honest at this size, and
    the reason it stops being an option is exactly the point worth making."""
    root = WORKSPACE / task["folder"]
    blocks = []
    for p in _workspace_files(task["folder"]):
        blocks.append(f"--- FILE: {p.relative_to(root)} ---\n{p.read_text(errors='replace')}")
    body = "\n\n".join(blocks)
    return f"{body}\n\n--- TASK ---\n{task['goal']}", len(blocks)


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj)}\n\n"


_THINK_TAG = re.compile(r"</?think>")


class ThinkFilter:
    """Strips inline <think>…</think> from a token stream.

    With reasoning turned off, some models still wrap a scratchpad in these
    tags inside the normal content channel — Ollama doesn't split it out, so
    the raw markup lands on screen mid-demo. Tags can also arrive split across
    chunks, hence the held-back tail.
    """

    def __init__(self):
        self.buf = ""
        self.inside = False

    def feed(self, text: str) -> str:
        self.buf += text
        out = []
        while (m := _THINK_TAG.search(self.buf)):
            if not self.inside:
                out.append(self.buf[:m.start()])
            self.inside = m.group() == "<think>"
            self.buf = self.buf[m.end():]
        if len(self.buf) > 8:  # keep a tail that might be a partial tag
            emit, self.buf = self.buf[:-8], self.buf[-8:]
            if not self.inside:
                out.append(emit)
        return "".join(out)

    def flush(self) -> str:
        rest = "" if self.inside else self.buf
        self.buf = ""
        return rest


def tally(task: dict, findings: list[dict]) -> tuple[str, str]:
    """(label, formatted value) for the run's headline number — computed here
    rather than trusted to the model, because an audience adds up the column."""
    if task["tally"] == "sum":
        total = sum(f["amount"] or 0 for f in findings)
        return task["tally_label"], f"${total:,.2f}"
    return task["tally_label"], str(len(findings))


def _cost(in_tokens: int, out_tokens: int) -> float:
    return round(in_tokens / 1e6 * tokens.CLOUD_IN_PER_M
                 + out_tokens / 1e6 * tokens.CLOUD_OUT_PER_M, 4)


async def _load_with_heartbeat(client: httpx.AsyncClient, model: str):
    """Yield status events while a cold model loads. Yields ('error', msg) last
    on failure, ('ready', None) on success."""
    if any(m["name"] == model for m in await _loaded_models(client)):
        err = await ensure_room(client, model)
        yield ("error", err) if err else ("ready", None)
        return
    prep = asyncio.ensure_future(_prepare_model(client, model))
    t0 = time.time()
    while True:
        done, _ = await asyncio.wait({prep}, timeout=3)
        if done:
            break
        yield ("status", f"Loading the model into memory… {int(time.time() - t0)}s")
    err = prep.result()
    yield ("error", err) if err else ("ready", None)


# ------------------------------------------------------------------ the runs

async def _stream_turn(client: httpx.AsyncClient, payload: dict, step: int, final: bool = False):
    """Stream one Ollama turn. Yields ("sse", event) as text arrives, then
    exactly one ("turn", {...}) with the assembled content, tool calls, and
    token counts — or ("error", message)."""
    content_parts: list[str] = []
    calls: list[dict] = []
    streamed = 0
    settled = False
    buf: list[str] = []
    think_buf: list[str] = []
    last_emit = 0.0
    p_in = p_out = 0
    kind = "report" if final else "delta"
    scrub = ThinkFilter()
    try:
        async with client.stream("POST", f"{OLLAMA}/api/chat", json=payload) as resp:
            if resp.status_code != 200:
                body = (await resp.aread()).decode(errors="replace")[:300]
                try:  # unwrap Ollama's nested error envelope
                    err = json.loads(body).get("error", body)
                    body = err.get("message", body) if isinstance(err, dict) else str(err)
                except ValueError:
                    pass
                yield ("error", body)
                return
            async for line in resp.aiter_lines():
                if not line.strip():
                    continue
                chunk = json.loads(line)
                msg = chunk.get("message", {})
                if msg.get("thinking"):
                    streamed += 1
                    tokens.add_pending()
                    think_buf.append(msg["thinking"])
                if msg.get("content"):
                    streamed += 1
                    tokens.add_pending()
                    visible = scrub.feed(msg["content"])
                    if visible:
                        content_parts.append(visible)
                        buf.append(visible)
                if msg.get("tool_calls"):
                    calls.extend(msg["tool_calls"])
                if (buf or think_buf) and time.time() - last_emit > 0.15:
                    last_emit = time.time()
                    yield ("sse", _sse({"type": kind, "step": step,
                                        "text": "".join(buf), "think": "".join(think_buf)}))
                    buf = []
                    think_buf = []
                if chunk.get("done"):
                    p_in = chunk.get("prompt_eval_count", 0)
                    p_out = chunk.get("eval_count", streamed)
                    tokens.settle_pending(streamed)
                    settled = True
    finally:
        if not settled and streamed:
            tokens.settle_pending(streamed)
            tokens.record(out_tokens=streamed)
    tail = scrub.flush()
    if tail:
        content_parts.append(tail)
        buf.append(tail)
    if buf or think_buf:
        yield ("sse", _sse({"type": kind, "step": step,
                            "text": "".join(buf), "think": "".join(think_buf)}))
    yield ("turn", {"content": "".join(content_parts), "calls": calls,
                    "in": p_in, "out": p_out})


async def run_generative(task_key: str, model: str, stuffed: bool = False):
    """One prompt in, one answer out — the workload the rest of this demo shows.

    `stuffed` inlines every workspace file into that one prompt instead of
    leaving the model empty-handed.
    """
    task = TASKS.get(task_key)
    if task is None:
        yield _sse({"type": "error", "error": f"Unknown task: {task_key}"})
        return
    mode = "stuffed" if stuffed else "generative"
    if stuffed:
        prompt, n_files = stuffed_prompt(task)
        system = STUFFED_SYSTEM
    else:
        prompt, n_files = task["goal"], 0
        system = GENERATIVE_SYSTEM
    async with httpx.AsyncClient(timeout=httpx.Timeout(900, connect=10)) as client:
        async for kind, payload in _load_with_heartbeat(client, model):
            if kind == "status":
                yield _sse({"type": "status", "status": payload})
            elif kind == "error":
                yield _sse({"type": "error", "error": payload})
                return
        yield _sse({"type": "start", "mode": mode, "task": task["label"],
                    "files_pasted": n_files})
        t0 = time.time()
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ]
        streamed = 0
        settled = False
        buf: list[str] = []
        last_emit = 0.0
        scrub = ThinkFilter()
        try:
            async with client.stream("POST", f"{OLLAMA}/api/chat",
                                     json={"model": model, "messages": messages,
                                           "stream": True, "think": False,
                                           # pinned so a presenter gets the same
                                           # answer they rehearsed with
                                           "options": {"temperature": 0,
                                                       "num_predict": 2000}}) as resp:
                if resp.status_code != 200:
                    body = (await resp.aread()).decode(errors="replace")[:300]
                    yield _sse({"type": "error", "error": body})
                    return
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    chunk = json.loads(line)
                    piece = chunk.get("message", {}).get("content", "")
                    if piece:
                        streamed += 1
                        tokens.add_pending()
                        buf.append(scrub.feed(piece))
                        if time.time() - last_emit > 0.15:
                            last_emit = time.time()
                            yield _sse({"type": "delta", "text": "".join(buf)})
                            buf = []
                    if chunk.get("done"):
                        p_in = chunk.get("prompt_eval_count", 0)
                        p_out = chunk.get("eval_count", streamed)
                        tokens.settle_pending(streamed)
                        settled = True
                        tokens.record(in_tokens=p_in, out_tokens=p_out)
                        yield _sse({"type": "delta", "text": "".join(buf) + scrub.flush()})
                        yield _sse({"type": "done", "mode": mode,
                                    "steps": 1, "tool_calls": 0,
                                    "files_pasted": n_files,
                                    "in_tokens": p_in, "out_tokens": p_out,
                                    "cost": _cost(p_in, p_out),
                                    "seconds": round(time.time() - t0, 1)})
        except httpx.HTTPError as e:
            yield _sse({"type": "error", "error": f"Ollama unreachable: {e}"})
        finally:
            if not settled and streamed:
                tokens.settle_pending(streamed)
                tokens.record(out_tokens=streamed)


async def run_agentic(task_key: str, model: str, reasoning: bool = False):
    """The loop: think → call a tool → read the result → repeat → report.

    Emits one `step_done` per iteration carrying that step's billed input
    tokens, which is where the cost curve becomes visible: step 9 pays for
    everything steps 1-8 put in the conversation.
    """
    global _running
    task = TASKS.get(task_key)
    if task is None:
        yield _sse({"type": "error", "error": f"Unknown task: {task_key}"})
        return
    if _running:
        yield _sse({"type": "error", "error": "An agent run is already going — one at a time on a demo box."})
        return
    _running = True
    folder = task["folder"]
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(900, connect=10)) as client:
            async for kind, payload in _load_with_heartbeat(client, model):
                if kind == "status":
                    yield _sse({"type": "status", "status": payload})
                elif kind == "error":
                    yield _sse({"type": "error", "error": payload})
                    return
            can_think = await supports_thinking(client, model)
            t0 = time.time()
            messages = [
                {"role": "system", "content": AGENT_SYSTEM},
                {"role": "user", "content": f"{task['goal']}\n\n{task['agent_hint']}"},
            ]
            yield _sse({"type": "start", "mode": "agentic", "task": task["label"],
                        "max_steps": MAX_STEPS})

            cum_in = cum_out = 0
            ctx_prev = 0        # running size of the whole conversation, in tokens
            tool_calls = 0
            seen_calls: set[str] = set()
            findings: list[dict] = []
            specs = tool_specs(task)
            step = 0
            nudges = 0

            while step < MAX_STEPS:
                step += 1
                yield _sse({"type": "step", "step": step})
                payload = {"model": model, "messages": messages,
                           "tools": specs, "stream": True,
                           # Bound each turn so a model that starts narrating
                           # its whole analysis can't run for minutes and blow
                           # out the context it then re-sends. Keep the ceiling
                           # generous: tool calls are emitted after the prose,
                           # so cutting a turn short can decapitate the call.
                           "options": {"temperature": 0, "num_predict": 2500}}
                if can_think:
                    payload["think"] = reasoning

                turn = {}
                async for kind, data in _stream_turn(client, payload, step):
                    if kind == "sse":
                        yield data
                    elif kind == "error":
                        yield _sse({"type": "error", "error": data})
                        return
                    else:
                        turn = data

                # What a cloud API would bill for this turn's input. Ollama's
                # prompt_eval_count is the full context when nothing is cached
                # and only the new tokens when the KV prefix is reused, so take
                # whichever reading yields the true conversation size.
                billed_in = turn["in"] if turn["in"] >= ctx_prev else ctx_prev + turn["in"]
                ctx_prev = billed_in + turn["out"]
                cum_in += billed_in
                cum_out += turn["out"]
                tokens.record(in_tokens=billed_in, out_tokens=turn["out"])

                content, calls = turn["content"], turn["calls"]
                yield _sse({"type": "step_done", "step": step,
                            "step_in": billed_in, "step_out": turn["out"],
                            "in_tokens": cum_in, "out_tokens": cum_out,
                            "cost": _cost(cum_in, cum_out),
                            "tool_calls": tool_calls,
                            "seconds": round(time.time() - t0, 1)})

                if not calls:
                    messages.append({"role": "assistant", "content": content})
                    # Stopping with an empty findings list usually means the
                    # model analysed everything in prose and never logged it —
                    # which would report "nothing found" on a pile of findings.
                    if not findings and nudges < 2:
                        nudges += 1
                        yield _sse({"type": "nudge", "step": step})
                        messages.append({"role": "user", "content": (
                            "You have not recorded a single finding yet, so the report "
                            "would come out empty. Go back through what you read and call "
                            "record_finding once for each one now. Do not write prose.")})
                        continue
                    break  # nothing left to look up — time to report

                messages.append({"role": "assistant", "content": content, "tool_calls": calls})
                for call in calls:
                    fn = call.get("function", {})
                    name = fn.get("name", "")
                    args = fn.get("arguments", {})
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except ValueError:
                            args = {}
                    tool_calls += 1
                    yield _sse({"type": "tool", "step": step, "name": name, "args": args})
                    # A model that loops on one identical call burns steps and
                    # context without learning anything — say so instead of
                    # handing back the same bytes again.
                    signature = f"{name}:{json.dumps(args, sort_keys=True)}"
                    before = len(findings)
                    if signature in seen_calls:
                        result = ("You already made this exact call and got the result above. "
                                  "Repeating it adds nothing. Either call a different tool or "
                                  "write your final report now.")
                        summary = "repeat call — skipped"
                    else:
                        seen_calls.add(signature)
                        result, summary = await asyncio.to_thread(
                            run_tool, folder, name, args, findings,
                            task["tally"] == "sum")
                    yield _sse({"type": "tool_result", "step": step, "name": name,
                                "summary": summary, "preview": result[:600],
                                "chars": len(result)})
                    if name == "record_finding" and len(findings) > before:
                        label, value = tally(task, findings)
                        yield _sse({"type": "finding", "step": step,
                                    "finding": findings[-1], "count": len(findings),
                                    "tally_label": label, "tally_value": value})
                    messages.append({"role": "tool", "tool_name": name, "content": result})

            # The deliverable is its own turn: the loop gathers evidence, this
            # writes the report. Asking for both in one turn is what produces
            # the wall of thinking-out-loud instead of something a customer
            # would actually read.
            step += 1
            yield _sse({"type": "step", "step": step, "reporting": True})
            label, value = tally(task, findings)
            # An empty findings list means the agent never logged anything —
            # almost always because it analysed in prose and ran out of steps.
            # Saying "nothing found" would be a confident lie in front of a
            # customer, so say what actually happened instead.
            if not findings:
                text = (f"**The agent did not finish.** It ran {step - 1} steps and "
                        f"{tool_calls} tool calls without recording a single finding, so "
                        f"there is nothing to report — this is not a clean result, it is "
                        f"an incomplete run. Try again, or pick a stronger model from the "
                        f"dropdown: smaller models sometimes analyse in prose and never "
                        f"commit their findings.")
                yield _sse({"type": "report", "step": step, "text": text})
                yield _sse({"type": "final", "text": text, "findings": [],
                            "tally_label": label, "tally_value": value})
                yield _sse({"type": "done", "mode": "agentic", "steps": step,
                            "tool_calls": tool_calls, "findings": 0,
                            "in_tokens": cum_in, "out_tokens": cum_out,
                            "cost": _cost(cum_in, cum_out),
                            "seconds": round(time.time() - t0, 1)})
                return
            if findings:
                lines = "\n".join(
                    f"- {f['subject']} — {f['issue']}"
                    + (f" — ${f['amount']:,.2f}" if f["amount"] is not None else "")
                    + (f"\n  {f['detail']}" if f["detail"] else "")
                    for f in findings)
                lines += f"\n\n{label}: {value}"
            else:
                lines = "(You recorded no findings.)"
            messages.append({"role": "user", "content": REPORT_TEMPLATE.format(
                findings=lines, tally_label=label, tally_value=value)})
            report_payload = {"model": model, "messages": messages, "stream": True,
                              "options": {"temperature": 0, "num_predict": 1200}}
            if can_think:
                report_payload["think"] = False
            turn = {}
            async for kind, data in _stream_turn(client, report_payload, step, final=True):
                if kind == "sse":
                    yield data
                elif kind == "error":
                    yield _sse({"type": "error", "error": data})
                    return
                else:
                    turn = data
            billed_in = turn["in"] if turn["in"] >= ctx_prev else ctx_prev + turn["in"]
            cum_in += billed_in
            cum_out += turn["out"]
            tokens.record(in_tokens=billed_in, out_tokens=turn["out"])
            yield _sse({"type": "step_done", "step": step,
                        "step_in": billed_in, "step_out": turn["out"],
                        "in_tokens": cum_in, "out_tokens": cum_out,
                        "cost": _cost(cum_in, cum_out),
                        "tool_calls": tool_calls,
                        "seconds": round(time.time() - t0, 1)})

            yield _sse({"type": "final", "text": turn["content"],
                        "findings": findings, "tally_label": label, "tally_value": value})
            yield _sse({"type": "done", "mode": "agentic", "steps": step,
                        "tool_calls": tool_calls, "findings": len(findings),
                        "in_tokens": cum_in, "out_tokens": cum_out,
                        "cost": _cost(cum_in, cum_out),
                        "seconds": round(time.time() - t0, 1)})
    except httpx.HTTPError as e:
        yield _sse({"type": "error", "error": f"Ollama unreachable: {e}"})
    finally:
        _running = False
