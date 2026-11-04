"""Docs Q&A — minimal in-memory RAG over uploaded PDFs.

PDF → text chunks → Ollama embeddings (nomic-embed-text) → cosine top-k →
context-stuffed chat. No vector database: numpy is plenty for demo-sized
documents, and there is nothing extra to babysit before a customer call.
"""

import io
import re
from pathlib import Path

import httpx
import numpy as np
from pypdf import PdfReader

from . import tokens
from .chat import EMBED_MODEL, OLLAMA

DOCS_DIR = Path(__file__).resolve().parent.parent / "docs"

CHUNK_CHARS = 1200
CHUNK_OVERLAP = 200
TOP_K = 5

# doc_id -> {"name": str, "chunks": [str], "vectors": np.ndarray}
_store: dict[str, dict] = {}


def _chunk(text: str) -> list[str]:
    text = re.sub(r"[ \t]+", " ", text)
    chunks = []
    pos = 0
    while pos < len(text):
        chunk = text[pos : pos + CHUNK_CHARS].strip()
        if chunk:
            chunks.append(chunk)
        pos += CHUNK_CHARS - CHUNK_OVERLAP
    return chunks


async def _embed(texts: list[str]) -> np.ndarray:
    async with httpx.AsyncClient(timeout=300) as client:
        r = await client.post(f"{OLLAMA}/api/embed", json={"model": EMBED_MODEL, "input": texts})
        r.raise_for_status()
    body = r.json()
    tokens.record(embed_tokens=body.get("prompt_eval_count", 0))
    vecs = np.array(body["embeddings"], dtype=np.float32)
    return vecs / np.linalg.norm(vecs, axis=1, keepdims=True)


async def ingest_pdf(name: str, data: bytes) -> dict:
    reader = PdfReader(io.BytesIO(data))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    chunks = _chunk(text)
    if not chunks:
        raise ValueError("No extractable text found in this PDF.")
    vectors = await _embed(chunks)
    doc_id = name
    _store[doc_id] = {"name": name, "chunks": chunks, "vectors": vectors, "pages": len(reader.pages)}
    return {"doc_id": doc_id, "name": name, "pages": len(reader.pages), "chunks": len(chunks)}


async def load_preloaded_docs() -> list[dict]:
    """Ingest bundled PDFs (docs/) and generated scenario docs (idempotent)."""
    results = []
    scenario_docs = DOCS_DIR.parent / "data" / "scenario-docs"
    pdfs = sorted(DOCS_DIR.glob("*.pdf")) + sorted(scenario_docs.glob("*.pdf"))
    for pdf in pdfs:
        if pdf.name not in _store:
            try:
                results.append(await ingest_pdf(pdf.name, pdf.read_bytes()))
            except Exception:
                continue
    return results


def list_docs() -> list[dict]:
    return [
        {"doc_id": k, "name": v["name"], "pages": v["pages"], "chunks": len(v["chunks"])}
        for k, v in _store.items()
    ]


async def retrieve(doc_id: str, question: str) -> list[dict]:
    doc = _store.get(doc_id)
    if doc is None:
        raise KeyError(f"Unknown document: {doc_id}")
    qvec = (await _embed([question]))[0]
    scores = doc["vectors"] @ qvec
    top = np.argsort(scores)[::-1][:TOP_K]
    return [{"chunk": doc["chunks"][i], "score": float(scores[i]), "index": int(i)} for i in top]


def build_rag_messages(question: str, passages: list[dict], doc_name: str) -> list[dict]:
    context = "\n\n---\n\n".join(p["chunk"] for p in passages)
    system = (
        "You are a helpful assistant answering questions about a document. "
        f"The document is: {doc_name}. Answer ONLY from the excerpts below. "
        "If the answer isn't in the excerpts, say so plainly. Be concise.\n\n"
        f"EXCERPTS:\n{context}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": question}]
