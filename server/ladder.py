"""Ladder — the same two models climb four rungs of increasing difficulty.

The rest of Model Comparison is a race: one prompt, two panes, watch the
tokens. That shows speed, and speed is the small model's argument. This shows
where speed stops being the argument.

Four tasks, ordered by how much the model has to hold in its head at once.
gemma4:e4b wins rung 1 on the clock, holds rung 2, and falls off at rung 3 —
the first task where it has to add a column of numbers and keep eleven other
rules true at the same time. qwen3.5:122b-a10b holds all four. Both are
resident in the same 122 GB pool while it happens, which is the whole pitch:
one box, the cheap model for commodity work and the expensive one for work
that has to be right, and no cloud round-trip for either.

Every prompt and every checker here was auditioned against the real models on
this box — three trials each, identical verdicts, no truncations. The request
options below are part of that audit, not defaults:

  think=false     with Ollama's reasoning channel on, the 122B spends 51 s and
                  1,438 tokens on the rung-1 JSON extraction, and rung 2 and
                  rung 3 verdicts INVERT for e4b. Applied identically to both
                  models, so the comparison stays apples-to-apples.
  temperature 0,  a presenter gets the answer they rehearsed with.
  seed 42
  num_predict     per task, sized so nothing truncates.

Change any of them and the scorecard is no longer the audited one.

Verdicts are stamped here, in Python, never read out of the model's own text —
the audience is watching a check they could run themselves.
"""

import asyncio
import json
import re
import time
from pathlib import Path

import httpx

from . import tokens
from .agentworks import ThinkFilter, _sse
from .chat import OLLAMA, _loaded_models, ensure_room, mem_available_gb

# The audited pairing. Not presenter-choosable: the audition only validated
# these two models against these four tasks, and a different pair has a
# different (unmeasured) scorecard.
SMALL_MODEL = "gemma4:e4b"
BIG_MODEL = "qwen3.5:122b-a10b"

# The 122B needs ~80 GB. `ensure_room` clears the box for it (in practice it
# evicts nemotron-3.5-lightning:30b, which the dashboard keeps warm). The small
# model then loads into what's left — see `_load_small` for why that one does
# NOT go through ensure_room.
SMALL_HEADROOM_GB = 6


def mem_total_gb() -> float:
    """Size of the unified pool. The prep beat counts gigabytes walking into
    it, and a fraction needs a denominator the audience can see."""
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemTotal:"):
            return int(line.split()[1]) / 2**20
    return 0.0


# ---------------------------------------------------------------- checkers
#
# Ported verbatim from the audition harness (tasks.py). Every character was
# audited for 3/3 deterministic verdicts — do not tidy these up. The only
# addition is the optional `detail` out-parameter on the two shared checkers,
# which collects material for the plain-language failure line and cannot
# change a verdict.

THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
THINK_OPEN_RE = re.compile(r"<think>.*", re.DOTALL | re.IGNORECASE)


def strip_think(text: str) -> str:
    t = THINK_RE.sub("", text)
    # unterminated think block (truncation) -> drop the rest
    t = THINK_OPEN_RE.sub("", t)
    return t.strip()


def words(s: str):
    return re.findall(r"[A-Za-z0-9'\-$%.,]+", s)


def norm(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def find_json(text: str):
    """Extract the first JSON object from text (handles ``` fences)."""
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if m:
        text = m.group(1)
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except Exception:
                    return None
    return None


def parse_md_table(text: str):
    """Return (headers, rows) for the first markdown pipe table found."""
    lines = [l for l in text.splitlines()]
    tbl = [l.strip() for l in lines if l.strip().startswith("|")]
    if len(tbl) < 3:
        return None, None

    def cells(line):
        parts = line.strip().strip("|").split("|")
        return [p.strip() for p in parts]

    headers = cells(tbl[0])
    body = []
    for l in tbl[1:]:
        c = cells(l)
        if all(re.fullmatch(r":?-{2,}:?", x) for x in c if x):
            continue
        body.append(c)
    return headers, body


def listed_items(text: str):
    """Pull enumerated / bulleted / newline items out of a list answer."""
    items = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = re.match(r"^(?:[-*•]|\d{1,2}[.)])\s+(.*)$", line)
        if m:
            line = m.group(1)
        elif line.startswith("|"):
            continue
        elif len(line.split()) > 12:
            continue  # prose
        else:
            # bare line, keep it
            pass
        line = line.strip()
        line = re.sub(r"^\*\*(.*?)\*\*.*$", r"\1", line)
        line = re.split(r"\s+[-–—(]\s*", line)[0]
        line = line.strip(" *_:;.,")
        if line:
            items.append(line)
    return items


# ---- rung 1: extract a memo into JSON

R1_MEMO = """INTERNAL MEMO - Northwind Logistics
From: Priya Raghavan, Ops Director
Date: 14 March 2025

The Ashford depot handled 4,820 outbound pallets last month, up from 4,115 in
February. Overtime spend reached $61,400, which is $9,200 above the approved
ceiling. Two refrigeration units (assets RF-118 and RF-204) failed on 3 March
and were repaired on 9 March at a combined cost of $12,750. Depot manager
Tomas Belliard has requested three additional forklift operators before the
Q2 peak, which begins on 1 June 2025. Customer complaints fell to 37, down
from 52. The depot's on-time delivery rate was 94.6 percent."""

R1A_PROMPT = f"""Read the memo below and extract the facts into JSON.

Reply with ONLY a JSON object, no commentary, using exactly these keys:
"depot" (string), "outbound_pallets" (integer), "overtime_spend_usd" (integer),
"repair_cost_usd" (integer), "complaints" (integer), "on_time_rate_percent" (number),
"depot_manager" (string).

MEMO:
{R1_MEMO}"""


def check_r1a(text, detail=None):
    obj = find_json(text)
    c = {}
    if obj is None:
        return False, {"valid_json": False}, "no parseable JSON object"
    c["valid_json"] = True
    want = {
        "depot": ("ashford",),
        "outbound_pallets": (4820,),
        "overtime_spend_usd": (61400,),
        "repair_cost_usd": (12750,),
        "complaints": (37,),
        "on_time_rate_percent": (94.6,),
        "depot_manager": ("tomas belliard",),
    }
    notes = []
    for k, exp in want.items():
        v = obj.get(k, None)
        ok = False
        if v is None:
            ok = False
        elif isinstance(exp[0], str):
            ok = exp[0] in norm(str(v))
        else:
            try:
                ok = abs(float(str(v).replace(",", "").replace("$", "")) - float(exp[0])) < 0.05
            except Exception:
                ok = False
        c[f"field_{k}"] = ok
        if not ok:
            notes.append(f"{k}={v!r}")
            if detail is not None:
                detail.setdefault("wrong", []).append((k, v, exp[0]))
    return all(c.values()), c, "; ".join(notes)


# ---- rung 2: name a closed set exactly

UN_LANGUAGES = {
    "arabic": ["arabic"], "chinese": ["chinese", "mandarin"], "english": ["english"],
    "french": ["french"], "russian": ["russian"], "spanish": ["spanish"],
}

R2D_PROMPT = """The United Nations has exactly six official languages.

List all six. Output ONLY a numbered list, one language per line, numbered 1 to 6,
with no commentary and no introduction."""

# ALTERNATE rung 2, fully audited but not wired up. Swapping the RUNGS entry at
# the bottom of this file from "r2d_un_languages" to "r2a_8000ers" changes the
# scorecard to ✓ ✗ ✗ ✗ — e4b lists fourteen peaks, twelve real, and invents
# "Gasherbrum III" (7,952 m) and "Shishapangan", while the 122B goes 14/14. It
# is the most vivid failure in the whole audition and the right pick if the
# room wants to see hallucination specifically; the shipped ladder keeps the
# win-hold-break-break shape instead. No UI for it: this is a one-line swap.
EIGHT_THOUSANDERS = {
    "everest": ["everest", "mount everest", "sagarmatha", "chomolungma"],
    "k2": ["k2", "godwin austen", "chhogori"],
    "kangchenjunga": ["kangchenjunga", "kanchenjunga", "kangchendzonga"],
    "lhotse": ["lhotse"],
    "makalu": ["makalu"],
    "cho oyu": ["cho oyu", "chooyu"],
    "dhaulagiri": ["dhaulagiri"],
    "manaslu": ["manaslu"],
    "nanga parbat": ["nanga parbat"],
    "annapurna": ["annapurna"],
    "gasherbrum i": ["gasherbrum i", "gasherbrum 1", "hidden peak"],
    "broad peak": ["broad peak"],
    "gasherbrum ii": ["gasherbrum ii", "gasherbrum 2"],
    "shishapangma": ["shishapangma", "shisha pangma", "gosainthan"],
}

R2A_PROMPT = """There are exactly fourteen mountains on Earth whose summits rise above
8,000 metres above sea level (the "eight-thousanders").

List all fourteen by name. Output ONLY a numbered list, one mountain name per line,
numbered 1 to 14, with no heights, no countries, no commentary and no introduction."""


def _set_check(text, truth: dict, n_expected: int, tag: str, detail=None):
    items = listed_items(text)
    matched = {}
    invented = []
    for it in items:
        ni = norm(it)
        if not ni:
            continue
        hit = None
        for key, aliases in truth.items():
            for a in aliases:
                if ni == a or re.search(r"\b" + re.escape(a) + r"\b", ni):
                    hit = key
                    break
            if hit:
                break
        if hit:
            matched.setdefault(hit, it)
        else:
            invented.append(it)
    missing = sorted(set(truth) - set(matched))
    c = {
        f"{tag}_count_{n_expected}": len(items) == n_expected,
        f"{tag}_all_present": len(matched) == len(truth),
        f"{tag}_no_invented": len(invented) == 0,
    }
    notes = f"listed={len(items)} matched={len(matched)} missing={missing} invented={invented}"
    if detail is not None:
        detail.update(listed=len(items), matched=len(matched),
                      missing=missing, invented=invented, expected=n_expected)
    return all(c.values()), c, notes


def check_r2d(text, detail=None):
    return _set_check(text, UN_LANGUAGES, 6, "languages", detail)


def check_r2a(text, detail=None):
    return _set_check(text, EIGHT_THOUSANDERS, 14, "peaks", detail)


# ---- rung 3: eleven simultaneous constraints (the fall-off)

def _r3_prompt(rows, total, extra_rule):
    return f"""Write a project status table for the "Halyard" data-centre migration.

You must obey ALL of the following rules exactly:
1. Output a markdown table, and beneath it exactly one sentence. Output nothing else --
   no preamble, no headings, no notes, no explanation.
2. The table must have exactly these four column headers, in this order:
   Phase | Owner | Days | Risk
3. The table must have exactly {rows} data rows (not counting the header row or the
   separator row).
4. Every Phase name must be exactly two words long.
5. Every Owner must be a single first name. All {rows} names must begin with a different
   letter, and the names must be listed in alphabetical order.
6. The numbers in the Days column must be whole numbers that add up to exactly {total}.
7. Every cell in the Risk column must be exactly three words long.
8. Exactly one Risk cell must contain the word "vendor", and the word "vendor" must not
   appear anywhere else in your output.
9. The words "will" and "migration" must not appear anywhere in your output.
10. The one sentence beneath the table must begin with the word "Overall", must contain
    12 words or fewer, must include the word "Halyard", and must end with an
    exclamation mark.{extra_rule}"""


R3G_PROMPT = _r3_prompt(6, 120,
    "\n11. The number in the Days column of the LAST row must be exactly equal to the sum\n"
    "    of the numbers in the Days column of the FIRST TWO rows.")


def _r3_check(text, rows_expected, total, extra=None, detail=None):
    t = text.strip()
    headers, rows = parse_md_table(t)
    c = {}
    if headers is None:
        return False, {"has_table": False}, "no markdown table found"
    c["has_table"] = True
    c["headers_exact"] = [norm(h) for h in headers] == ["phase", "owner", "days", "risk"]
    c["row_count"] = len(rows) == rows_expected
    full = [r for r in rows if len(r) >= 4]
    c["phase_two_words"] = bool(full) and all(len(words(r[0])) == 2 for r in full)
    owners = [r[1].strip() for r in full]
    c["owner_single_name"] = len(owners) == rows_expected and all(
        re.fullmatch(r"[A-Za-z'\-]+", o or "") for o in owners)
    c["owner_distinct_initials"] = len({o[:1].lower() for o in owners if o}) == rows_expected
    c["owner_alphabetical"] = owners == sorted(owners, key=str.lower) and len(owners) == rows_expected
    days, days_ok = [], True
    for r in full:
        m = re.fullmatch(r"\d+", r[2].strip())
        if m:
            days.append(int(m.group(0)))
        else:
            days_ok = False
    c["days_sum"] = days_ok and len(days) == rows_expected and sum(days) == total
    c["risk_three_words"] = bool(full) and all(len(words(r[3])) == 3 for r in full)
    c["vendor_exactly_once"] = len(re.findall(r"\bvendor\b", t, re.IGNORECASE)) == 1
    c["vendor_in_risk"] = sum(1 for r in full if re.search(r"\bvendor\b", r[3], re.IGNORECASE)) == 1
    c["no_will_no_migration"] = (re.search(r"\bwill\b", t, re.IGNORECASE) is None
                                 and re.search(r"\bmigration\b", t, re.IGNORECASE) is None)
    lines = t.splitlines()
    tbl_idx = [i for i, l in enumerate(lines) if l.strip().startswith("|")]
    tail = " ".join(lines[max(tbl_idx) + 1:]).strip() if tbl_idx else ""
    c["no_preamble"] = " ".join(lines[:min(tbl_idx)]).strip() == "" if tbl_idx else False
    c["tail_starts_overall"] = tail.lower().startswith("overall")
    c["tail_one_sentence"] = len([s for s in re.split(r"(?<=[.!?])\s+", tail) if s.strip()]) == 1
    c["tail_max_12_words"] = 0 < len(words(tail)) <= 12
    c["tail_has_halyard"] = "halyard" in tail.lower()
    c["tail_ends_bang"] = tail.endswith("!")
    if extra:
        extra(c, days)
    notes = (f"rows={len(rows)} days={days} sum={sum(days)} owners={owners} "
             f"risks={[r[3] for r in full]} tail={tail[:80]!r}")
    if detail is not None:
        detail.update(days=days, total=total, rows=len(rows),
                      rows_expected=rows_expected, owners=owners, tail=tail)
    return all(c.values()), c, notes


def check_r3g(text, detail=None):
    def extra(c, days):
        c["days_last_eq_first_two"] = len(days) == 6 and days[5] == days[0] + days[1]
    return _r3_check(text, 6, 120, extra, detail)


# ---- rung 4: an eight-step chain that reaches backwards

R4D_PROMPT = """Work through the following quarterly settlement. Each step uses results from
earlier steps -- note that some steps refer back to an EARLIER step, not the one immediately
before. Show your working for every step, then give the final answer on its own final line
in exactly this format:

FINAL: <number>

Step 1. Warehouse A shipped 18,450 units. Warehouse B shipped 62% as many units as
        Warehouse A. How many units did Warehouse B ship, rounded DOWN to a whole unit?
Step 2. Add Warehouse A's units and Warehouse B's units to get the combined unit total.
Step 3. 4.5% of the combined units were returned. Subtract the returned units from the
        combined total and round DOWN to a whole number of units. Call this the net units.
Step 4. Net units sell at $23.40 each. Compute the gross revenue.
Step 5. Subtract sales commission from the gross revenue. The commission rate is chosen
        using the number of units WAREHOUSE B shipped in Step 1:
          - fewer than 10,000 units: 3%
          - 10,000 to 11,499 units: 5%
          - 11,500 units or more: 8%
Step 6. Add a freight surcharge of $1.15 for every NET unit from Step 3.
Step 7. Subtract a storage fee of $0.30 for every unit WAREHOUSE A shipped in Step 1.
Step 8. Round the result to the nearest cent. That is the final answer."""

# Verified in Python before any model was run:
# B = floor(18450*0.62) = 11439 -> combined 29889 -> returns 1345.005
# -> net floor(28543.995) = 28543 -> gross 667906.20 -> 5% band -> 634510.89
# -> +freight 32824.45 = 667335.34 -> -storage 5535 = 661800.34
R4D_ANSWER = 661800.34


def _final_number(text):
    ms = re.findall(r"FINAL\s*:\s*\$?\s*(-?[\d,]+(?:\.\d+)?)", text, re.IGNORECASE)
    if not ms:
        return None
    try:
        return float(ms[-1].replace(",", ""))
    except Exception:
        return None


def _make_chain_check(answer, tol=0.005):
    def _c(text, detail=None):
        v = _final_number(text)
        c = {"has_final_line": v is not None,
             "correct_value": v is not None and abs(v - answer) <= tol}
        if detail is not None:
            detail.update(final=v, expected=answer)
        return all(c.values()), c, f"final={v} expected={answer}"
    return _c


check_r4d = _make_chain_check(R4D_ANSWER)


# --------------------------------------------------- plain-language failures
#
# The scorecard chip says ✗. This says why, in words an audience can check
# without knowing anything about the task.

R1_SOURCE = {
    "depot": "the memo says Ashford",
    "outbound_pallets": "the memo says 4,820",
    "overtime_spend_usd": "the memo says $61,400",
    "repair_cost_usd": "the memo says $12,750",
    "complaints": "the memo says 37",
    "on_time_rate_percent": "the memo says 94.6",
    "depot_manager": "the memo says Tomas Belliard",
}

R3_RULES = {
    "has_table": "No markdown table in the answer at all.",
    "headers_exact": "The column headers aren't exactly Phase | Owner | Days | Risk.",
    "row_count": "The table doesn't have exactly 6 data rows.",
    "phase_two_words": "At least one Phase name isn't exactly two words.",
    "owner_single_name": "At least one Owner isn't a single first name.",
    "owner_distinct_initials": "Two owners start with the same letter.",
    "owner_alphabetical": "The owners aren't in alphabetical order.",
    "risk_three_words": "At least one Risk cell isn't exactly three words.",
    "vendor_exactly_once": "The word “vendor” appears more than once (or not at all).",
    "vendor_in_risk": "“Vendor” isn't in exactly one Risk cell.",
    "no_will_no_migration": "The banned words “will” or “migration” appear.",
    "no_preamble": "There's a preamble above the table.",
    "tail_starts_overall": "The closing sentence doesn't start with “Overall”.",
    "tail_one_sentence": "There's more than one sentence beneath the table.",
    "tail_max_12_words": "The closing sentence is longer than 12 words.",
    "tail_has_halyard": "The closing sentence doesn't mention Halyard.",
    "tail_ends_bang": "The closing sentence doesn't end with an exclamation mark.",
}

_NUM = r"[\d,]+(?:\.\d+)?"
# One line: "28,544 × 23.40 = 667,329.60"
_MULT_INLINE = re.compile(rf"({_NUM})\s*(?:×|\*)\s*({_NUM})\s*=\s*({_NUM})")
# Two lines: "Gross revenue = 28,544 × 23.40" then "Gross revenue = 667,329.60"
_MULT_LHS = re.compile(rf"^(.+?)\s*=\s*({_NUM})\s*(?:×|\*)\s*({_NUM})\s*$")
_MULT_RHS = re.compile(rf"^(.+?)\s*=\s*({_NUM})\s*$")


def _num(s: str) -> float:
    return float(s.replace(",", ""))


def _plain_math(text: str) -> str:
    """Drop the LaTeX these models wrap arithmetic in, so a product reads as
    plain digits: `$28,544 \\times \\$23.40$` -> `28,544 × 23.40`."""
    t = re.sub(r"\\(?:times|cdot)\b", "×", text)
    t = t.replace("\\$", "").replace("$", "").replace("\\,", "")
    return re.sub(r"\*\*", "", t)


def _bad_product(a: float, b: float, got: float):
    """(expected, got) when a stated product contradicts its own operands."""
    if a < 10 or b < 1:  # percentages and rates — float noise, not theatre
        return None
    want = a * b
    return (want, got) if abs(got - want) > max(0.01, abs(want) * 1e-9) else None


def _self_contradicting_product(text: str):
    """Find a product the model wrote that doesn't equal its own operands.

    Rung 4's headline failure is arithmetic the audience can check on a phone:
    e4b writes 28,544 × $23.40 = $667,329.60 when its own numbers give
    $667,929.60. Pointing at that beats arguing about rounding order, which is
    the other slip and the arguable one.
    """
    lines = _plain_math(text).splitlines()
    for i, line in enumerate(lines):
        m = _MULT_INLINE.search(line)
        if m:
            bad = _bad_product(_num(m.group(1)), _num(m.group(2)), _num(m.group(3)))
            if bad:
                return f"{m.group(1)} × {m.group(2)} = {m.group(3)}", *bad
        m = _MULT_LHS.match(line.strip())
        if not m:
            continue
        label = norm(m.group(1))
        # the answer usually lands on the next line, restated under the same label
        for follow in lines[i + 1:i + 4]:
            r = _MULT_RHS.match(follow.strip())
            if not r or norm(r.group(1)) != label:
                continue
            bad = _bad_product(_num(m.group(2)), _num(m.group(3)), _num(r.group(2)))
            if bad:
                return f"{m.group(2)} × {m.group(3)} = {r.group(2)}", *bad
            break
    return None


def explain(rung_key: str, checks: dict, detail: dict, text: str) -> str:
    """One or two sentences saying what broke. Never speculation — every claim
    is read back out of the model's own output."""
    failed = [k for k, v in checks.items() if not v]
    if rung_key == "r1a_extract_json":
        if not checks.get("valid_json", True):
            return "The reply wasn't a parseable JSON object."
        bits = [f"{k} (came back {v!r}, {R1_SOURCE.get(k, 'wrong')})"
                for k, v, _ in detail.get("wrong", [])]
        return f"{len(bits)} of the 7 fields are wrong: " + "; ".join(bits) + "."
    if rung_key.startswith("r2"):
        n = detail.get("expected", 0)
        parts = [f"Listed {detail.get('listed', 0)} items; "
                 f"{detail.get('matched', 0)} of the {n} are correct."]
        if detail.get("invented"):
            parts.append("Invented: " + ", ".join(detail["invented"]) + ".")
        if detail.get("missing"):
            parts.append("Missing: " + ", ".join(
                m.title() for m in detail["missing"]) + ".")
        return " ".join(parts)
    if rung_key == "r3g_relational":
        if not checks.get("has_table", True):
            return R3_RULES["has_table"]
        lead = []
        days = detail.get("days") or []
        if not checks.get("days_sum", True):
            lead.append(f"The Days column adds up to {sum(days)} — the task required "
                        f"{detail.get('total', 120)}.")
        if not checks.get("days_last_eq_first_two", True) and len(days) == 6:
            lead.append(f"The last row's Days value is {days[5]}, but the first two rows "
                        f"add up to {days[0] + days[1]}.")
        rest = [R3_RULES[k] for k in failed if k in R3_RULES and k != "days_sum"]
        held = len(checks) - len(failed)
        return (f"{held} of {len(checks)} constraints held. "
                + " ".join(lead + rest))
    if rung_key == "r4d_settlement":
        if not checks.get("has_final_line", True):
            return "No “FINAL:” line in the answer."
        got, want = detail.get("final"), detail.get("expected")
        parts = []
        bad = _self_contradicting_product(text)
        if bad:
            expr, should, wrote = bad
            parts.append(f"Its own arithmetic contradicts itself: it wrote “{expr}”, "
                         f"but that product is {should:,.2f} — out by "
                         f"{abs(should - wrote):,.2f}, and every later step inherits it.")
        parts.append(f"Final answer {got:,.2f}; the settlement works out to {want:,.2f}.")
        return " ".join(parts)
    return "; ".join(failed)


# Eight-word versions of the same thing, for the verdict that lands on screen in
# 40-point type. Same source material as `explain` — read out of the model's own
# answer, never a guess — just cut to what a room can take in at a glance.

R3_SHORT = {
    "headers_exact": "Wrong column headers",
    "row_count": "Wrong number of rows",
    "phase_two_words": "A phase name isn't two words",
    "owner_single_name": "An owner isn't a single name",
    "owner_distinct_initials": "Two owners share a first letter",
    "owner_alphabetical": "Owners out of alphabetical order",
    "risk_three_words": "A risk cell isn't three words",
    "vendor_exactly_once": "“Vendor” doesn't appear exactly once",
    "vendor_in_risk": "“Vendor” landed outside the risk column",
    "no_will_no_migration": "Used a banned word",
    "no_preamble": "Wrote a preamble it was told not to",
    "tail_starts_overall": "Closing line doesn't start with “Overall”",
    "tail_one_sentence": "More than one closing sentence",
    "tail_max_12_words": "Closing sentence runs past 12 words",
    "tail_has_halyard": "Closing line never says Halyard",
    "tail_ends_bang": "No exclamation mark at the end",
}


def short_reason(rung_key: str, checks: dict, detail: dict, text: str) -> str:
    """The headline failure in eight words or fewer."""
    failed = [k for k, v in checks.items() if not v]
    if rung_key == "r1a_extract_json":
        if not checks.get("valid_json", True):
            return "Not valid JSON"
        wrong = detail.get("wrong") or []
        if len(wrong) == 1:
            return f"Got {wrong[0][0].replace('_', ' ')} wrong"
        return f"{len(wrong)} of 7 fields wrong"
    if rung_key.startswith("r2"):
        invented = detail.get("invented") or []
        missing = detail.get("missing") or []
        n = detail.get("expected", 0)
        if invented:
            return (f"Invented “{invented[0]}”" if len(invented) == 1
                    else f"Invented {len(invented)} that don't exist")
        if missing:
            return (f"Missed {missing[0].title()}" if len(missing) == 1
                    else f"Missed {len(missing)} of the {n}")
        return f"Listed {detail.get('listed', 0)}, not {n}"
    if rung_key == "r3g_relational":
        if not checks.get("has_table", True):
            return "No table at all"
        days = detail.get("days") or []
        if not checks.get("days_sum", True) and days:
            return f"Sums to {sum(days)}, not {detail.get('total', 120)}"
        if not checks.get("days_last_eq_first_two", True) and len(days) == 6:
            return f"Last row is {days[5]}, not {days[0] + days[1]}"
        for k in failed:
            if k in R3_SHORT:
                return R3_SHORT[k]
        return f"{len(checks) - len(failed)} of {len(checks)} rules held"
    if rung_key == "r4d_settlement":
        if not checks.get("has_final_line", True):
            return "No final answer given"
        bad = _self_contradicting_product(text)
        if bad:
            _, should, wrote = bad
            return f"Its own math, off by ${abs(should - wrote):,.0f}"
        got, want = detail.get("final"), detail.get("expected")
        if got is not None and want is not None:
            return f"Off by ${abs(got - want):,.0f}"
        return "Wrong final answer"
    return failed[0].replace("_", " ") if failed else "Failed"


# ------------------------------------------------------------------- rungs

RUNGS = [
    {
        "n": 1, "key": "r1a_extract_json", "title": "Read and extract",
        # `stage_title` is what fills the screen: five words at most, read from
        # the back of a room. `title` stays as the prose name for anything that
        # needs a sentence to sit in.
        "stage_title": "Pull seven facts",
        "stage_sub": "Seven numbers and names out of an ops memo, as strict JSON.",
        "rung_label": "Commodity work",
        "desc": "Pull seven facts out of an operations memo into strict JSON.",
        "checks": "Parses the JSON and compares all seven values against the memo.",
        "prompt": R1A_PROMPT, "check": check_r1a, "num_predict": 1200,
    },
    {
        "n": 2, "key": "r2d_un_languages", "title": "Name a closed set",
        "stage_title": "Name all six",
        "stage_sub": "The six official languages of the UN — no more, no less.",
        "rung_label": "Knowledge depth",
        "desc": "List the six official languages of the United Nations — no more, no less.",
        "checks": "Counts correct items, missing items and invented items separately.",
        "prompt": R2D_PROMPT, "check": check_r2d, "num_predict": 2200,
        # ALTERNATE (see the r2a block above): swap in R2A_PROMPT / check_r2a /
        # the eight-thousanders title and the scorecard becomes ✓ ✗ ✗ ✗.
    },
    {
        "n": 3, "key": "r3g_relational", "title": "Hold eleven rules at once",
        "stage_title": "Hold eleven rules",
        "stage_sub": "One table, eleven formatting and arithmetic rules at the same time.",
        "rung_label": "Held constraints",
        "desc": "Build a project table under eleven simultaneous formatting and arithmetic rules.",
        "checks": "Nineteen independent checks — including whether the Days column really adds to 120.",
        "prompt": R3G_PROMPT, "check": check_r3g, "num_predict": 2200,
    },
    {
        "n": 4, "key": "r4d_settlement", "title": "Follow the chain",
        "stage_title": "Follow eight steps",
        "stage_sub": "A settlement chain where later steps reach back to earlier ones.",
        "rung_label": "Multi-step reasoning",
        "desc": "An eight-step settlement where later steps reach back to earlier ones.",
        "checks": "Compares the FINAL: line against $661,800.34, computed independently in Python.",
        "prompt": R4D_PROMPT, "check": check_r4d, "num_predict": 2600,
    },
]

SIDES = [("small", SMALL_MODEL), ("big", BIG_MODEL)]

_running = False

# `prompt` rides along so the page can show the exact words the model was given
# without a second endpoint — that is the whole of the skeptic's path.
_INFO_KEYS = ("n", "key", "title", "stage_title", "stage_sub", "rung_label",
              "desc", "checks", "prompt")


def rung_info() -> list[dict]:
    return [{k: r[k] for k in _INFO_KEYS} for r in RUNGS]


async def info() -> dict:
    """Pairing + rung metadata + whether each model is installed and resident,
    so the page can warn before the presenter presses a dead button."""
    models = {"small": {"name": SMALL_MODEL, "role": "The small one"},
              "big": {"name": BIG_MODEL, "role": "The big one"}}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            tags = await client.get(f"{OLLAMA}/api/tags")
            tags.raise_for_status()
            sizes = {m["name"]: m["size"] / 2**30 for m in tags.json().get("models", [])}
            resident = {m["name"] for m in await _loaded_models(client)}
        for side in models.values():
            side["installed"] = side["name"] in sizes
            side["resident"] = side["name"] in resident
            side["size_gb"] = round(sizes.get(side["name"], 0), 1)
            if not side["installed"]:
                side["hint"] = f"ollama pull {side['name']}"
        ollama_up = True
    except (httpx.HTTPError, ValueError):
        ollama_up = False
        for side in models.values():
            side.update(installed=False, resident=False, size_gb=0,
                        hint=f"ollama pull {side['name']}")
    return {
        "models": models,
        "rungs": rung_info(),
        "ollama_up": ollama_up,
        "ready": ollama_up and all(s["installed"] for s in models.values()),
        "free_gb": round(mem_available_gb(), 1),
        "pool_gb": round(mem_total_gb()),
    }


# ------------------------------------------------------------------ the run

async def _load(client: httpx.AsyncClient, model: str, size_gb: float = 0.0):
    """Load `model` with keep_alive, yielding ('status', text, data) heartbeats
    while it walks into memory. Ends with ('ready', …) or ('error', …).

    The heartbeat carries how many gigabytes have actually landed — MemAvailable
    before the load minus MemAvailable now. On GB10 there is no separate VRAM
    figure to read (unified memory; nvidia-smi reports N/A), so /proc/meminfo IS
    the measurement, and the number on screen is a real one.
    """
    prep = asyncio.ensure_future(client.post(
        f"{OLLAMA}/api/generate",
        json={"model": model, "keep_alive": "30m"}, timeout=1200))
    t0 = time.time()
    free0 = mem_available_gb()
    while True:
        # 0.4 s, not 2 s: this drives a bar the room is watching fill.
        done, _ = await asyncio.wait({prep}, timeout=0.4)
        if done:
            break
        elapsed = time.time() - t0
        free = mem_available_gb()
        walked = max(0.0, free0 - free)
        if size_gb:
            walked = min(walked, size_gb)
        yield ("status", f"Loading {model} into unified memory… {int(elapsed)}s",
               {"phase": "loading", "model": model, "size_gb": round(size_gb, 1),
                "loaded_gb": round(walked, 1), "elapsed": round(elapsed, 1),
                "free_gb": round(free, 1), "pool_gb": round(mem_total_gb())})
    try:
        prep.result().raise_for_status()
    except (httpx.HTTPError, asyncio.CancelledError) as e:
        yield ("error", f"Couldn't load {model}: {e}", None)
        return
    elapsed = time.time() - t0
    yield ("ready", f"{model} resident after {elapsed:.0f}s",
           {"phase": "resident", "model": model, "size_gb": round(size_gb, 1),
            "loaded_gb": round(size_gb, 1), "elapsed": round(elapsed, 1),
            "free_gb": round(mem_available_gb(), 1), "pool_gb": round(mem_total_gb())})


async def _model_sizes(client: httpx.AsyncClient) -> dict:
    try:
        r = await client.get(f"{OLLAMA}/api/tags", timeout=10)
        r.raise_for_status()
        return {m["name"]: m["size"] / 2**30 for m in r.json().get("models", [])}
    except (httpx.HTTPError, ValueError, KeyError):
        return {}


async def _prep(client: httpx.AsyncClient):
    """Get both models resident. Yields ('status'|'error', text, data|None)."""
    loaded = {m["name"] for m in await _loaded_models(client)}
    sizes = await _model_sizes(client)
    pool = round(mem_total_gb())

    if BIG_MODEL not in loaded:
        yield ("status", "Clearing unified memory for the 122B — about 80 GB of model "
                         "is about to walk in.",
               {"phase": "clearing", "model": BIG_MODEL, "pool_gb": pool,
                "size_gb": round(sizes.get(BIG_MODEL, 0), 1),
                "free_gb": round(mem_available_gb(), 1)})
        err = await ensure_room(client, BIG_MODEL)
        if err:
            yield ("error", err, None)
            return
        async for kind, msg, data in _load(client, BIG_MODEL, sizes.get(BIG_MODEL, 0)):
            yield (kind, msg, data)
            if kind == "error":
                return
    else:
        yield ("status", f"{BIG_MODEL} is already resident.",
               {"phase": "resident", "model": BIG_MODEL, "elapsed": 0, "pool_gb": pool,
                "size_gb": round(sizes.get(BIG_MODEL, 0), 1),
                "loaded_gb": round(sizes.get(BIG_MODEL, 0), 1),
                "free_gb": round(mem_available_gb(), 1)})

    if SMALL_MODEL not in loaded:
        # Deliberately NOT ensure_room(): it evicts everything that isn't the
        # model being loaded, which would throw away the 80 GB we just spent
        # 22 s loading. The audited pairing coexists with ~22 GB to spare, so a
        # plain headroom check is the right guard here.
        free = mem_available_gb()
        if free < SMALL_HEADROOM_GB:
            yield ("error", f"Only {free:.0f} GB free after loading the 122B — not enough "
                            f"room for {SMALL_MODEL} alongside it. Free some memory and "
                            f"try again.", None)
            return
        async for kind, msg, data in _load(client, SMALL_MODEL, sizes.get(SMALL_MODEL, 0)):
            yield (kind, msg, data)
            if kind == "error":
                return
    else:
        yield ("status", f"{SMALL_MODEL} is already resident.",
               {"phase": "resident", "model": SMALL_MODEL, "elapsed": 0, "pool_gb": pool,
                "size_gb": round(sizes.get(SMALL_MODEL, 0), 1),
                "loaded_gb": round(sizes.get(SMALL_MODEL, 0), 1),
                "free_gb": round(mem_available_gb(), 1)})

    free = mem_available_gb()
    yield ("status", f"Both models resident, {free:.0f} GB of the pool "
                     f"still free. Climbing.",
           {"phase": "ready", "pool_gb": pool, "free_gb": round(free, 1),
            "resident_gb": round(sizes.get(BIG_MODEL, 0) + sizes.get(SMALL_MODEL, 0), 1)})


async def _run_rung(client: httpx.AsyncClient, rung: dict, side: str, model: str):
    """Stream one model through one rung, then stamp the verdict here.

    Yields ("sse", event) as text arrives, then exactly one ("state", str) —
    "pass", "fail" or "error" — for the running scorecard. The request options
    below are the audited ones; see the module docstring before touching any.
    """
    yield ("sse", _sse({"type": "model_start", "rung": rung["n"],
                        "side": side, "model": model}))
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": rung["prompt"]}],
        "stream": True,
        "think": False,
        "keep_alive": "30m",
        "options": {"temperature": 0, "num_predict": rung["num_predict"], "seed": 42},
    }
    t0 = time.time()
    full: list[str] = []
    buf: list[str] = []
    last_emit = 0.0
    streamed = 0
    settled = False
    # think=false is pinned, but a model can still wrap a scratchpad in literal
    # <think> tags inside the content channel — don't put that on a demo screen.
    scrub = ThinkFilter()
    try:
        async with client.stream("POST", f"{OLLAMA}/api/chat", json=payload) as resp:
            if resp.status_code != 200:
                body = (await resp.aread()).decode(errors="replace")[:300]
                yield ("sse", _sse({"type": "verdict", "rung": rung["n"], "side": side,
                                    "state": "error", "explain": body,
                                    "fail_short": "Ollama refused the request"}))
                yield ("state", "error")
                return
            done_reason = None
            out_tokens = 0
            async for line in resp.aiter_lines():
                if not line.strip():
                    continue
                chunk = json.loads(line)
                piece = chunk.get("message", {}).get("content", "")
                if piece:
                    streamed += 1
                    tokens.add_pending()
                    full.append(piece)
                    buf.append(scrub.feed(piece))
                    if time.time() - last_emit > 0.12:
                        last_emit = time.time()
                        # `tok` is the running chunk count — the pane's rising
                        # token counter, so a 32 s answer never looks frozen.
                        yield ("sse", _sse({"type": "delta", "rung": rung["n"],
                                            "side": side, "text": "".join(buf),
                                            "tok": streamed}))
                        buf = []
                if chunk.get("done"):
                    done_reason = chunk.get("done_reason")
                    out_tokens = chunk.get("eval_count", streamed)
                    tokens.settle_pending(streamed)
                    settled = True
                    tokens.record(in_tokens=chunk.get("prompt_eval_count", 0),
                                  out_tokens=out_tokens)
            yield ("sse", _sse({"type": "delta", "rung": rung["n"], "side": side,
                                "text": "".join(buf) + scrub.flush(),
                                "tok": streamed}))
    except httpx.HTTPError as e:
        yield ("sse", _sse({"type": "verdict", "rung": rung["n"], "side": side,
                            "state": "error", "explain": f"Ollama unreachable: {e}",
                            "fail_short": "Ollama stopped answering"}))
        yield ("state", "error")
        return
    finally:
        if not settled and streamed:
            tokens.settle_pending(streamed)
            tokens.record(out_tokens=streamed)

    seconds = round(time.time() - t0, 1)
    if done_reason == "length":
        # The reply hit num_predict and stopped mid-thought. That is an invalid
        # trial, not a wrong answer — never score it as a ✗.
        yield ("sse", _sse({"type": "verdict", "rung": rung["n"], "side": side,
                            "state": "error", "seconds": seconds, "tokens": out_tokens,
                            "fail_short": "Cut off at the token limit",
                            "explain": "The reply was cut off at the token limit, so this "
                                       "rung wasn't a fair trial — it isn't scored."}))
        yield ("state", "error")
        return

    clean = strip_think("".join(full))
    detail: dict = {}
    passed, checks, notes = rung["check"](clean, detail)
    yield ("sse", _sse({
        "type": "verdict", "rung": rung["n"], "side": side, "model": model,
        "state": "pass" if passed else "fail",
        "seconds": seconds, "tokens": out_tokens,
        "explain": "" if passed else explain(rung["key"], checks, detail, clean),
        # eight words for the screen; `explain` is the paragraph underneath it
        "fail_short": "" if passed else short_reason(rung["key"], checks, detail, clean),
        "notes": notes,
    }))
    yield ("state", "pass" if passed else "fail")


# The closing slide. PUNCHLINE is the one huge line — eight words, and it has to
# stay true to a scorecard of ✓ ✓ ✗ ✗ against ✓ ✓ ✓ ✓. PITCH is the single
# supporting line under it; SUMMARY is the paragraph, kept for the drawer where
# a skeptic goes looking for the argument in full.
PUNCHLINE = "Fast until it has to be right."
PITCH = ("Both models sat in the same {pool} GB pool the whole time — so you pick "
         "per task, not per contract.")

SUMMARY = (
    "Same box, same memory pool, same two models loaded the whole time. The small one "
    "is the faster answer on commodity work and it is genuinely good at it — right up "
    "to the rung where the answer has to be checkable. The big one costs seconds and "
    "gigabytes and gets all four. That is the choice a GB10 lets you make per task "
    "instead of per contract: cheap and fast for volume, heavyweight for work that has "
    "to be right, and both of them sitting in one 122 GB pool with nothing leaving the "
    "building."
)


async def run():
    """SSE stream for the whole ladder: prep, then rung by rung, small first."""
    global _running
    if _running:
        yield _sse({"type": "error",
                    "error": "A ladder run is already going — one at a time on a demo box."})
        return
    _running = True
    scorecard = {"small": [None] * len(RUNGS), "big": [None] * len(RUNGS)}
    try:
        yield _sse({"type": "start",
                    "models": {"small": SMALL_MODEL, "big": BIG_MODEL},
                    "rungs": rung_info()})
        async with httpx.AsyncClient(timeout=httpx.Timeout(1200, connect=10)) as client:
            try:
                async for kind, msg, data in _prep(client):
                    if kind == "error":
                        yield _sse({"type": "error", "error": msg})
                        return
                    # `status` stays exactly what it was — the structured
                    # progress fields ride alongside it.
                    yield _sse({"type": "status", "status": msg, **(data or {})})
            except httpx.HTTPError as e:
                yield _sse({"type": "error", "error": f"Ollama unreachable: {e}"})
                return

            for i, rung in enumerate(RUNGS):
                yield _sse({"type": "rung_start", "rung": rung["n"],
                            "of": len(RUNGS),
                            "title": rung["title"], "rung_label": rung["rung_label"],
                            "stage_title": rung["stage_title"],
                            "stage_sub": rung["stage_sub"], "prompt": rung["prompt"],
                            "desc": rung["desc"], "checks": rung["checks"]})
                # small first: it is the one that finishes quickly, and the
                # audience should see the speed win before the accuracy loss
                for side, model in SIDES:
                    async for kind, payload in _run_rung(client, rung, side, model):
                        if kind == "sse":
                            yield payload
                        else:
                            scorecard[side][i] = payload
                    yield _sse({"type": "scorecard", "scorecard": scorecard})
                yield _sse({"type": "rung_done", "rung": rung["n"]})

        pool = round(mem_total_gb())
        yield _sse({"type": "done", "scorecard": scorecard, "summary": SUMMARY,
                    "punchline": PUNCHLINE, "pitch": PITCH.format(pool=pool),
                    "pool_gb": pool})
    except asyncio.CancelledError:
        # client navigated away or hit Stop — nothing to clean up, the models
        # stay warm for the next run
        raise
    finally:
        _running = False
