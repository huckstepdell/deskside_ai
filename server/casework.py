"""Case Works — a guided public-safety workflow demo: one case, three desks.

Stage 1 (patrol): messy field notes -> structured draft incident report.
Stage 2 (detective): case bundle -> timeline + contradiction flags + leads.
Stage 3 (analyst): 10 incident summaries -> linked-case pattern detection.

Each stage forces the model into a JSON schema (Ollama structured outputs),
so the frontend renders real documents — a paper report, a timeline, linked
case cards — instead of chat text. All case data is fictional (Springfield)
and deliberately contains plants: one witness contradiction in stage 2 and
one three-case MO pattern in stage 3, so the "aha" is repeatable.
"""

import json
import time

import httpx

from . import tokens
from .chat import OLLAMA, ensure_room, supports_thinking

# The configured workhorse with reasoning off: benchmarked ~18s/stage and
# still catches the planted contradiction (thinking-on takes ~3x longer
# for marginal gain here)
from .config import role as _role

DEFAULT_MODEL = _role("workhorse")

# ---------------------------------------------------------------- stage data

FIELD_NOTES = """2317 hrs cleared scene, hartwell electronics 412 commerce st, burglary/commercial
rear service door pried near latch, crowbar-type tool marks
mgr on site = renee calloway, provided inventory export, approx $48,200 missing - portable gpus, laptops, networking gear
stockroom shelving emptied, 2 units
shoe impression in dust nr rear entry, hex pattern tread, est size 10-11, cast taken -> E-1
blue fiber on damaged door frame -> E-2
alarm log export -> E-3: armed 2015 by calloway, disarmed 2147 w/ shared staff code
mgr says code circulated for 2+ yrs, ex employees know it
lot cam -> E-4: light colored panel van arr 2132, obscured plate, dep 2158
neighbor dana okafor (fairview bakery) heard metallic banging 2130-2145, saw van at loading dock
mgr notes: missing items restocked only 2 days ago. similar unsolved hit at their fairview location last month
requested traffic cam pulls for commerce st
follow ups: ex-employee list from mgr, fairview case file, alarm co for code change records"""

CASE_BUNDLE = [
    {"id": "alarm-log", "title": "Alarm panel export (E-3)", "body":
        "System armed 20:15 by user CALLOWAY-R. Rear door sensor: OPEN 21:33, tamper alert suppressed "
        "21:33-21:47 (local siren only). System disarmed 21:47 with staff code 4412. Rear door sensor "
        "OPEN/CLOSE events between 21:49 and 21:58. No further events. System never re-armed."},
    {"id": "camera-log", "title": "Parking-lot camera review (E-4)", "body":
        "Light-colored panel van enters lot 21:32, reverses to loading dock. Plate obscured by fabric or "
        "tape. Two subjects visible, faces not resolvable. Van departs 21:58 heading north on Commerce "
        "Street. No other vehicle activity between 21:00 and 23:00."},
    {"id": "witness-okafor", "title": "Witness statement — Dana Okafor (neighboring business owner)", "body":
        "I own the bakery next door. I was closing up and took the recycling out around 21:40. I heard "
        "metallic banging start maybe ten minutes before that, around 21:30, and it went on and off until "
        "about 21:45. When I was outside I saw a light-colored van backed up against Hartwell's loading "
        "dock. I didn't see any people. I went back inside and left around 22:00."},
    {"id": "witness-webb", "title": "Witness statement — Marcus Webb (passerby)", "body":
        "I was walking home from the gym and passed Hartwell Electronics at about 22:15. I clearly saw two "
        "men loading boxes into a white van at the side of the building. One was tall, wearing a dark "
        "hoodie. I remember checking my phone right then, and it said 22:14. They didn't seem in a hurry."},
    {"id": "witness-calloway", "title": "Statement — Renee Calloway (store manager)", "body":
        "I closed the store myself and set the alarm at 20:15. The missing stock arrived only two days "
        "earlier — it was the restock after the same items sold out following our Fairview store's "
        "break-in last month. The staff disarm code hasn't been changed in over two years; a lot of "
        "former employees would still know it. Only current staff should know restock timing, though "
        "the delivery schedule is posted in the shared back office."},
]

INCIDENTS = [
    {"case": "26-03771", "title": "Fairview Office & Computer Supply — burglary", "summary":
        "Rear service door pried near latch after close. High-value electronics taken two days after a "
        "large restock. Neighbor reported a light panel van behind the building. Partial shoe impression "
        "in dust, hexagonal tread. Alarm disarmed with a valid staff code. Unsolved."},
    {"case": "26-03954", "title": "Residential garage theft", "summary":
        "Theft of two bicycles from an unlocked residential garage on Maple Court during the afternoon. "
        "No forced entry and no vehicle involved; the resident suspects a neighborhood juvenile seen "
        "walking a third bike earlier that day."},
    {"case": "26-04101", "title": "Storefront smash-and-grab — jewelry", "summary":
        "Front display window broken with a paver at 03:10. Grab of display trays within 40 seconds. Two "
        "subjects on foot, fled toward the riverwalk. Loud, fast, no vehicle observed."},
    {"case": "26-04187", "title": "Hartwell Electronics — burglary", "summary":
        "Rear service door pried near latch. $48,200 in GPUs, laptops and networking gear taken two days "
        "after restock. Light panel van with obscured plate on lot camera, 26-minute dwell. Hexagonal "
        "tread shoe impression recovered. Alarm disarmed with shared staff code during the entry window."},
    {"case": "26-04220", "title": "Catalytic converter thefts — commuter lot", "summary":
        "Four vehicles at the Park & Ride, converters cut with a battery-powered saw. No entry into the "
        "vehicles and nothing else taken; referred to the regional task force handling saw-cut "
        "converter thefts."},
    {"case": "26-04310", "title": "Retail shoplifting — grocery", "summary":
        "Subject concealed liquor bottles in a duffel bag and left without paying. Store recovered "
        "merchandise at the door. Subject cited and released."},
    {"case": "26-04366", "title": "Vehicle break-ins — parking structure", "summary":
        "Six vehicles on level 3 with windows punched, gym bags and electronics taken from seats. "
        "Occurred during evening event traffic. One subject on camera, on foot, hooded."},
    {"case": "26-04411", "title": "Vandalism — underpass graffiti", "summary":
        "Fresh tags on the Commerce Street underpass matching a known moniker. Paint cans recovered "
        "nearby. Referred to the graffiti abatement program."},
    {"case": "26-04477", "title": "Copper wire theft — construction site", "summary":
        "Spooled copper taken from a fenced site on Industrial Parkway. Chain-link cut at the rear, bolt "
        "cutters. Flatbed or pickup suspected given the load weight."},
    {"case": "26-04502", "title": "Northgate Computer Supply — burglary", "summary":
        "Rear door pried near the latch overnight. Laptops and network switches taken; delivery of that "
        "stock had arrived 48 hours earlier. A light-colored van seen on a neighboring business camera. "
        "Hex-pattern shoe impression photographed inside the stockroom. Alarm log shows a valid-code "
        "disarm the owner cannot account for."},
]

# ------------------------------------------------------------- stage schemas

REPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "case_number": {"type": "string"},
        "offense_classification": {"type": "string"},
        "location": {"type": "string"},
        "occurred_window": {"type": "string"},
        "reporting_officer": {"type": "string"},
        "narrative": {"type": "string"},
        "persons": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "role": {"type": "string"}, "details": {"type": "string"}},
            "required": ["name", "role", "details"]}},
        "evidence": {"type": "array", "items": {"type": "object", "properties": {
            "tag": {"type": "string"}, "item": {"type": "string"}, "location_found": {"type": "string"}},
            "required": ["tag", "item", "location_found"]}},
        "follow_ups": {"type": "array", "items": {"type": "string"}},
        "review_flags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["case_number", "offense_classification", "location", "occurred_window",
                 "narrative", "persons", "evidence", "follow_ups", "review_flags"],
}

ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "timeline": {"type": "array", "items": {"type": "object", "properties": {
            "time": {"type": "string"}, "event": {"type": "string"}, "source": {"type": "string"}},
            "required": ["time", "event", "source"]}},
        "contradictions": {"type": "array", "items": {"type": "object", "properties": {
            "description": {"type": "string"},
            "source_a": {"type": "string"}, "quote_a": {"type": "string"},
            "source_b": {"type": "string"}, "quote_b": {"type": "string"}},
            "required": ["description", "source_a", "quote_a", "source_b", "quote_b"]}},
        "observations": {"type": "array", "items": {"type": "string"}},
        "open_leads": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["timeline", "contradictions", "observations", "open_leads"],
}

PATTERNS_SCHEMA = {
    "type": "object",
    "properties": {
        "linked_groups": {"type": "array", "items": {"type": "object", "properties": {
            "case_numbers": {"type": "array", "items": {"type": "string"}},
            "shared_attributes": {"type": "array", "items": {"type": "string"}},
            "rationale": {"type": "string"},
            "confidence": {"type": "string"}},
            "required": ["case_numbers", "shared_attributes", "rationale", "confidence"]}},
        "recommended_actions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["linked_groups", "recommended_actions"],
}

# ------------------------------------------------------------- stage prompts

def _bundle_text() -> str:
    return "\n\n".join(f"=== {d['title']} ===\n{d['body']}" for d in CASE_BUNDLE)


def _incidents_text() -> str:
    return "\n\n".join(f"CASE {i['case']} — {i['title']}\n{i['summary']}" for i in INCIDENTS)


def build_stage(stage: str, notes: str | None) -> tuple[list[dict], dict] | None:
    """Messages + JSON schema for a stage; None if the stage is unknown."""
    if stage == "report":
        return ([
            {"role": "system", "content":
                "You convert a patrol officer's raw field notes into a draft incident report for the "
                "Springfield Police Department records system. Use ONLY facts present in the notes — "
                "never invent details. Expand shorthand into clear, professional, past-tense narrative "
                "prose in chronological order. Times use 24-hour format. follow_ups are the "
                "investigative tasks still to do. review_flags are different and must not repeat "
                "follow_ups: they call out statements inside this report that are estimates, "
                "secondhand, or unverified (e.g. 'loss value is per the manager's export, unverified', "
                "'occurred window is inferred from the alarm log'). Assign case number "
                "26-04187 and reporting officer 'OFC. A. DEMO #4821'. The incident date is March 14; "
                "use that date rather than placeholders."},
            {"role": "user", "content": f"FIELD NOTES:\n{notes or FIELD_NOTES}"},
        ], REPORT_SCHEMA)
    if stage == "analysis":
        return ([
            {"role": "system", "content":
                "You are assisting a detective reviewing a case bundle for a commercial burglary "
                "(Springfield PD case 26-04187). Build a minute-by-minute timeline from the documents, "
                "citing the source document for every entry. Then compare the accounts and flag "
                "contradictions — but only genuine logical incompatibilities, where the statements "
                "cannot both be true (an account of events at a time other records prove impossible, "
                "mutually exclusive facts). Differences in vantage point, precision, or wording are "
                "not contradictions; nearby times are not contradictions. Expect one or two at most. "
                "Quote the conflicting passages; write each description as one specific sentence "
                "stating exactly what conflicts, most significant first. Note investigatively "
                "significant observations (e.g. what the entry method plus the alarm behavior implies). "
                "Base everything strictly on the documents."},
            {"role": "user", "content": f"CASE BUNDLE:\n\n{_bundle_text()}"},
        ], ANALYSIS_SCHEMA)
    if stage == "patterns":
        return ([
            {"role": "system", "content":
                "You are a crime analyst reviewing recent incident summaries for Springfield PD. "
                "Identify which cases are likely connected (same actors or MO), citing the specific "
                "shared attributes. Link cases only when they share multiple concrete physical or "
                "temporal details (same vehicle description, same tool marks, same entry method, same "
                "timing pattern relative to events). A link requires at least three concrete shared "
                "details. Generic similarities — 'both are thefts', 'both "
                "happened overnight', 'both involve property' — never justify a link. It is normal for "
                "most cases to remain unlinked; do not force groups. Then recommend concrete next "
                "actions for each linked series."},
            {"role": "user", "content": f"RECENT INCIDENTS:\n\n{_incidents_text()}"},
        ], PATTERNS_SCHEMA)
    return None


# --------------------------------------------------------------- the runner

def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj)}\n\n"


async def run(stage: str, notes: str | None, model: str = DEFAULT_MODEL):
    """SSE: status -> progress ticks -> result (parsed JSON) -> done stats."""
    built = build_stage(stage, notes)
    if built is None:
        yield _sse({"type": "error", "error": f"Unknown stage: {stage}"})
        return
    messages, schema = built
    start = time.time()
    streamed = 0
    settled = False
    content: list[str] = []
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(600, connect=10)) as client:
            err = await ensure_room(client, model)
            if err:
                yield _sse({"type": "error", "error": err})
                return
            yield _sse({"type": "status", "status": "Reading the case material…"})
            payload = {"model": model, "messages": messages, "stream": True, "format": schema,
                       "options": {"temperature": 0}}  # deterministic → repeatable demo runs
            if await supports_thinking(client, model):
                payload["think"] = False  # stages are schema-driven; speed wins here
            async with client.stream("POST", f"{OLLAMA}/api/chat", json=payload) as resp:
                if resp.status_code != 200:
                    body = (await resp.aread()).decode(errors="replace")[:300]
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
                    if streamed and streamed % 25 == 0:
                        yield _sse({"type": "progress", "tokens": streamed,
                                    "seconds": round(time.time() - start, 1)})
                    if chunk.get("done"):
                        tokens.settle_pending(streamed)
                        settled = True
                        tokens.record(in_tokens=chunk.get("prompt_eval_count", 0),
                                      out_tokens=chunk.get("eval_count", streamed))
        text = "".join(content)
        try:
            result = json.loads(text)
        except ValueError:
            yield _sse({"type": "error", "error": "The model returned malformed JSON — run it again."})
            return
        if stage == "patterns" and isinstance(result.get("linked_groups"), list):
            # a "series" of one case isn't a link — drop stragglers deterministically
            result["linked_groups"] = [
                g for g in result["linked_groups"] if len(g.get("case_numbers", [])) >= 2]
        yield _sse({"type": "result", "stage": stage, "data": result})
        yield _sse({"type": "done", "tokens": streamed, "seconds": round(time.time() - start, 1)})
    except httpx.HTTPError as e:
        yield _sse({"type": "error", "error": f"Ollama unreachable: {e}"})
    finally:
        if not settled and streamed:
            tokens.settle_pending(streamed)
            tokens.record(out_tokens=streamed)
