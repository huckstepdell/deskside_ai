"""Vertical use-case scenarios: built-in, custom, and AI-generated.

Built-ins live in scenarios/ (tracked — the curated verticals). Custom ones
live in data/scenarios/ (gitignored — an SE's own customer scenarios),
created by hand-editing a generated draft in the Scenario Studio. Custom
sample documents (fictional PDFs for Docs Q&A) land in data/scenario-docs/.

Generation uses the configured workhorse with structured outputs and a
system prompt that enforces the fictional-data discipline; the SE edits
before saving — the model drafts, the human judges.
"""

import json
import re
import time

import httpx

from . import tokens
from .chat import OLLAMA, ensure_room, supports_thinking
from .config import ROOT, role

BUILTIN_DIR = ROOT / "scenarios"
CUSTOM_DIR = ROOT / "data" / "scenarios"
DOC_DIR = ROOT / "data" / "scenario-docs"


def _load_dir(path, custom):
    out = []
    for f in sorted(path.glob("*.json")):
        try:
            s = json.loads(f.read_text())
            s["custom"] = custom
            out.append(s)
        except (OSError, ValueError):
            continue
    return out


def list_scenarios() -> list[dict]:
    builtin = sorted(_load_dir(BUILTIN_DIR, False), key=lambda s: s.get("order", 99))
    custom = sorted(_load_dir(CUSTOM_DIR, True), key=lambda s: s.get("title", ""))
    return builtin + custom


def slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower())[:40].strip("-") or "scenario"
    return f"custom-{slug}"


def save_custom(s: dict) -> dict:
    """Persist a custom scenario (validated shape, forced custom id space)."""
    for key in ("title", "tagline", "scenario"):
        if not isinstance(s.get(key), str) or not s[key].strip():
            raise ValueError(f"Scenario is missing '{key}'")
    if not s.get("id", "").startswith("custom-"):
        s["id"] = slugify(s["title"])
    s["nav"] = (s.get("nav") or s["title"])[:24]
    # normalize model quirks: pre-numbered steps, target-as-label ctas
    s["steps"] = [re.sub(r"^\s*\d+[.)]\s*", "", x) for x in s.get("steps", [])]
    for c in s.get("ctas", []):
        if c.get("target") and c.get("label", "").strip().lower() == c["target"]:
            c["label"] = {"chat": "Run it live in Chat →",
                          "rag": "Ask the document →",
                          "vision": "Analyze a photo →"}.get(c["target"], "Run it live →")
    s.setdefault("ctas", [])
    s.setdefault("why", [])
    s.setdefault("notes", [])
    s["playbooks"] = s.get("playbooks", [])
    CUSTOM_DIR.mkdir(parents=True, exist_ok=True)
    (CUSTOM_DIR / f"{s['id']}.json").write_text(json.dumps(s, indent=2, ensure_ascii=False))
    return s


def delete_custom(sid: str) -> bool:
    if not sid.startswith("custom-"):
        return False  # built-ins are not deletable from the UI
    f = CUSTOM_DIR / f"{sid}.json"
    if f.exists():
        f.unlink()
        pdf = DOC_DIR / f"{sid}-SAMPLE.pdf"
        pdf.unlink(missing_ok=True)
        return True
    return False


# ---------------------------------------------------------------- generation

SCENARIO_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "nav": {"type": "string"},
        "tagline": {"type": "string"},
        "scenario": {"type": "string"},
        "steps": {"type": "array", "items": {"type": "string"}},
        "ctas": {"type": "array", "items": {"type": "object", "properties": {
            "label": {"type": "string"},
            "target": {"type": "string", "enum": ["chat", "rag", "vision", "doc"]},
            "prompt": {"type": "string"}},
            "required": ["label", "target", "prompt"]}},
        "why": {"type": "array", "items": {"type": "object", "properties": {
            "head": {"type": "string"}, "body": {"type": "string"}},
            "required": ["head", "body"]}},
        "notes": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "nav", "tagline", "scenario", "steps", "ctas", "why", "notes"],
}

DOC_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "sections": {"type": "array", "items": {"type": "object", "properties": {
            "heading": {"type": "string"}, "body": {"type": "string"}},
            "required": ["heading", "body"]}},
    },
    "required": ["title", "sections"],
}

# ------------------------------------------------- document-output demos

DOCOUT_SCHEMA = {
    "type": "object",
    "properties": {
        "doc_type": {"type": "string"},
        "org": {"type": "string"},
        "title": {"type": "string"},
        "meta": {"type": "array", "items": {"type": "object", "properties": {
            "label": {"type": "string"}, "value": {"type": "string"}},
            "required": ["label", "value"]}},
        "sections": {"type": "array", "items": {"type": "object", "properties": {
            "heading": {"type": "string"}, "body": {"type": "string"}},
            "required": ["heading", "body"]}},
        "checklist": {"type": "array", "items": {"type": "string"}},
        "signoff": {"type": "string"},
    },
    "required": ["doc_type", "org", "title", "meta", "sections", "signoff"],
}

DOCOUT_SYSTEM = """You draft professional FICTIONAL business documents for AI demos — the
kind a customer in the relevant industry would recognize instantly: inspection
reports, compliance filings, prior-authorization letters, incident summaries,
work orders.

Rules: every name, organization, address, ID and number is invented; use the
fictional city 'Springfield' where a place is needed. Fill the structure:
- doc_type: the document category as it would appear on letterhead
  (e.g. 'PUMP STATION INSPECTION REPORT')
- org: the fictional issuing organization
- title: the specific subject line
- meta: 4-6 header fields (Date, ID/Reference, Prepared by, Location, Status…)
- sections: 3-6 substantive sections with specific, realistic detail —
  numbers, findings, requirements. Plain prose, no markdown.
- checklist: action items or required follow-ups, if the document type has
  them (else empty)
- signoff: preparer line, e.g. 'Prepared by J. Alvarez, Senior Inspector —
  DRAFT PENDING REVIEW'"""


async def generate_docout(prompt: str):
    """A demo deliverable: structured document from a scenario CTA prompt."""
    yield _sse({"type": "status", "status": "Drafting the document…"})
    async for ev in _generate(DOCOUT_SYSTEM, prompt, DOCOUT_SCHEMA):
        yield _sse(ev)


def build_docout_pdf(doc: dict) -> bytes:
    """The rendered document as a downloadable PDF (fictional stamp included)."""
    import io

    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    styles = getSampleStyleSheet()
    h = ParagraphStyle("H", parent=styles["Heading2"], spaceBefore=16)
    body = ParagraphStyle("B", parent=styles["BodyText"], fontSize=10.5, leading=15)
    small = ParagraphStyle("S", parent=styles["BodyText"], fontSize=8.5, textColor="#8a2020")
    flow = [Paragraph(str(doc.get("org", "")), styles["Heading3"]),
            Paragraph(str(doc.get("doc_type", "")), styles["Title"]),
            Paragraph("FICTIONAL SAMPLE — generated for AI demonstration; all details invented.", small),
            Spacer(1, 8),
            Paragraph(str(doc.get("title", "")), styles["Heading2"]), Spacer(1, 6)]
    meta = [[m.get("label", ""), m.get("value", "")] for m in doc.get("meta", [])]
    if meta:
        t = Table(meta, colWidths=[1.6 * inch, 4.6 * inch])
        t.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 9),
                               ("TEXTCOLOR", (0, 0), (0, -1), "#555555"),
                               ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
        flow.append(t)
    for s in doc.get("sections", []):
        flow += [Paragraph(s.get("heading", ""), h), Paragraph(s.get("body", ""), body)]
    if doc.get("checklist"):
        flow.append(Paragraph("Required follow-ups", h))
        for c in doc["checklist"]:
            flow.append(Paragraph(f"☐ {c}", body))
    flow += [Spacer(1, 14), Paragraph(str(doc.get("signoff", "")), body)]
    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=letter,
                      topMargin=0.8 * inch, bottomMargin=0.8 * inch).build(flow)
    return buf.getvalue()


GEN_SYSTEM = """You write demo scenario pages for a Dell Pro Max GB10 — a desk-side AI
supercomputer that runs large AI models fully locally (private, offline-capable,
no per-token cloud costs). A sales engineer will present this page to a customer.

Write in the second person to the presenter, concrete and confident, no hype
words like 'seamless' or 'revolutionize'. Structure:
- title: short page title for this industry/customer use case
- nav: 1-3 word sidebar label
- tagline: one sentence, the pitch
- scenario: 3-4 sentences painting one specific worker's before/after moment
- steps: 3 short 'run it live' presenter steps matching the ctas. Do NOT
  number them — they render as a numbered list already.
- ctas: 1-3 demo launch buttons. label is a short action phrase ending in an
  arrow, e.g. 'Draft the inspection report →' — NEVER just the target name.
  target 'doc' produces a formatted professional document (letterhead,
  fields, sections) — use it whenever the demo output is a deliverable the
  industry would recognize: reports, letters, filings, work orders.
  target 'chat' opens a chat with the prompt
  pre-filled; 'rag' opens document Q&A with the prompt as the question;
  'vision' opens photo analysis with the prompt as the instruction. Write
  prompts the model will actually receive — realistic, specific, and if they
  involve people or records, clearly fictional (invented names, 'FICTIONAL
  SAMPLE' markers).
- why: exactly 3 bullets on why local AI wins for THIS industry (head = 2-4
  words, body = one concrete sentence; compliance and data-sensitivity
  arguments specific to the industry beat generic privacy claims)
- notes: 2-3 presenter notes — pitfalls, what to emphasize, what NOT to demo

Everything must be plain text (no markdown, no HTML tags)."""

DOC_SYSTEM = """You write FICTIONAL sample documents for AI demos. The document will be
indexed into a document-Q&A system so a sales engineer can demonstrate asking
questions against it in front of a customer from this industry.

Rules: all names, organizations, addresses, and numbers are invented; use the
fictional city 'Springfield'; the title MUST contain 'FICTIONAL SAMPLE'; write
5-7 substantial sections with specific, queryable details (numbers, dates,
requirements, procedures) so Q&A demos have real material to find; plain text
only."""


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj)}\n\n"


async def _generate(system: str, user: str, schema: dict):
    """Structured generation with progress events; yields dicts then result."""
    model = role("workhorse")
    async with httpx.AsyncClient(timeout=httpx.Timeout(600, connect=10)) as client:
        err = await ensure_room(client, model)
        if err:
            yield {"type": "error", "error": err}
            return
        payload = {"model": model, "stream": True, "format": schema,
                   "options": {"temperature": 0.7},  # drafts should vary — the SE picks
                   "messages": [{"role": "system", "content": system},
                                {"role": "user", "content": user}]}
        if await supports_thinking(client, model):
            payload["think"] = False
        streamed = 0
        settled = False
        content = []
        t0 = time.time()
        try:
            async with client.stream("POST", f"{OLLAMA}/api/chat", json=payload) as resp:
                if resp.status_code != 200:
                    yield {"type": "error", "error": (await resp.aread()).decode(errors="replace")[:200]}
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
                    if streamed % 25 == 0 and streamed:
                        yield {"type": "progress", "tokens": streamed,
                               "seconds": round(time.time() - t0, 1)}
                    if chunk.get("done"):
                        tokens.settle_pending(streamed)
                        settled = True
                        tokens.record(in_tokens=chunk.get("prompt_eval_count", 0),
                                      out_tokens=chunk.get("eval_count", streamed))
        finally:
            if not settled and streamed:
                tokens.settle_pending(streamed)
                tokens.record(out_tokens=streamed)
        try:
            yield {"type": "result", "data": json.loads("".join(content))}
        except ValueError:
            yield {"type": "error", "error": "The model returned malformed JSON — try again."}


async def generate_scenario(industry: str, workflows: str, sensitive: str, extra: str):
    user = (f"Industry / customer: {industry}\n"
            f"What their people actually do all day: {workflows}\n"
            f"Data sensitivity / constraints: {sensitive or 'not specified'}\n"
            f"Specifics from the customer conversation: {extra or 'none'}\n\n"
            "Write the scenario page.")
    yield _sse({"type": "status", "status": "Drafting the scenario…"})
    async for ev in _generate(GEN_SYSTEM, user, SCENARIO_SCHEMA):
        yield _sse(ev)


async def generate_doc(scenario: dict):
    user = (f"The demo scenario this document supports:\n"
            f"Title: {scenario.get('title')}\nIndustry pitch: {scenario.get('tagline')}\n"
            f"Story: {scenario.get('scenario')}\n\n"
            "Write one fictional sample document this industry would recognize "
            "instantly (a policy, report, procedure, or case record) that the "
            "demo's document-Q&A questions could be asked against.")
    yield _sse({"type": "status", "status": "Writing the sample document…"})
    result = None
    async for ev in _generate(DOC_SYSTEM, user, DOC_SCHEMA):
        if ev["type"] == "result":
            result = ev["data"]
        else:
            yield _sse(ev)
    if result is None:
        return
    if "FICTIONAL" not in result.get("title", "").upper():
        result["title"] = f"{result.get('title', 'Sample Document')} (FICTIONAL SAMPLE)"
    sid = scenario.get("id") or slugify(scenario.get("title", "scenario"))
    DOC_DIR.mkdir(parents=True, exist_ok=True)
    pdf_path = DOC_DIR / f"{sid}-SAMPLE.pdf"
    _build_pdf(pdf_path, result)
    from . import rag  # index it for Docs Q&A immediately
    rag._store.pop(pdf_path.name, None)  # regeneration replaces the old index
    await rag.load_preloaded_docs()
    yield _sse({"type": "doc_done", "file": pdf_path.name, "title": result["title"],
                "sections": len(result["sections"])})


def _build_pdf(path, doc: dict) -> None:
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    styles = getSampleStyleSheet()
    h = ParagraphStyle("H", parent=styles["Heading2"], spaceBefore=18)
    body = ParagraphStyle("B", parent=styles["BodyText"], fontSize=11, leading=16)
    flow = [Paragraph(doc["title"], styles["Title"]), Spacer(1, 6),
            Paragraph("This is a fictional sample generated for AI demonstration purposes. "
                      "All names, organizations, and details are invented.", body)]
    for s in doc["sections"]:
        flow += [Paragraph(s["heading"], h), Paragraph(s["body"], body)]
    SimpleDocTemplate(str(path), pagesize=letter,
                      topMargin=0.9 * inch, bottomMargin=0.9 * inch).build(flow)
