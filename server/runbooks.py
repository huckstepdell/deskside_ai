"""Curated NVIDIA DGX Spark playbooks, served from the local clone.

The full repo (~49 playbooks) lives at ~/dgx-spark-playbooks. We surface a
curated subset of flagship runbooks — already downloaded, ready to walk
through offline — and render their markdown in the dashboard.
"""

import os
from pathlib import Path

PLAYBOOKS_ROOT = Path(os.environ.get("RUNBOOKS_DIR") or
                      Path.home() / "dgx-spark-playbooks" / "nvidia")
SITE_BASE = "https://build.nvidia.com/spark"

# Curated, demo-ordered subset. (slug = directory name in the repo)
CURATED = [
    {"slug": "ollama", "title": "Ollama", "tag": "Inference",
     "blurb": "The easiest way to run LLMs locally — powers this dashboard's Chat demo.",
     "status": "Installed & running on this device"},
    {"slug": "open-webui", "title": "Open WebUI + Ollama", "tag": "Inference",
     "blurb": "A full-featured ChatGPT-style web interface on top of Ollama."},
    {"slug": "llama-cpp", "title": "llama.cpp", "tag": "Inference",
     "blurb": "Lightweight, CUDA-accelerated inference engine — image pre-built on this device.",
     "status": "Docker image ready"},
    {"slug": "vllm", "title": "vLLM", "tag": "Inference",
     "blurb": "Production-grade, high-throughput inference server.",
     "status": "Docker image ready"},
    {"slug": "trt-llm", "title": "TensorRT-LLM", "tag": "Inference",
     "blurb": "NVIDIA's fastest path for LLM inference on Blackwell.",
     "status": "Docker image ready"},
    {"slug": "comfy-ui", "title": "ComfyUI", "tag": "Image & Video",
     "blurb": "Node-based studio for image and video generation workflows."},
    {"slug": "flux-finetuning", "title": "FLUX.1 LoRA Fine-tuning", "tag": "Fine-tuning",
     "blurb": "Teach an image model your own subject — fine-tuning on your desk, not in the cloud."},
    {"slug": "pytorch-fine-tune", "title": "Fine-tune with PyTorch", "tag": "Fine-tuning",
     "blurb": "Classic LLM fine-tuning workflow using the 128GB unified memory."},
    {"slug": "multi-agent-chatbot", "title": "Multi-Agent Chatbot", "tag": "Agents",
     "blurb": "Multiple AI agents collaborating on tasks, all running locally."},
    {"slug": "cuda-x-data-science", "title": "CUDA-X Data Science", "tag": "Data Science",
     "blurb": "GPU-accelerated pandas, UMAP and HDBSCAN — data science beyond LLMs."},
]


def _readme_meta(readme: Path) -> tuple[str, str]:
    """Title and one-line blurb from a playbook README (`# Title` / `> blurb`)."""
    title, blurb = "", ""
    try:
        for line in readme.read_text(encoding="utf-8", errors="replace").splitlines()[:20]:
            if line.startswith("# ") and not title:
                title = line[2:].strip()
            elif line.startswith("> ") and not blurb:
                blurb = line[2:].strip()
            if title and blurb:
                break
    except OSError:
        pass
    return title, blurb


def _local_slugs() -> set[str]:
    try:
        return {d.name for d in PLAYBOOKS_ROOT.iterdir() if (d / "README.md").exists()}
    except OSError:
        return set()


def list_runbooks() -> list[dict]:
    """Curated flagships first (pinned, with demo blurbs), then everything
    else discovered in the local clone — the page always reflects the repo."""
    out = []
    local = _local_slugs()
    for item in CURATED:
        out.append({
            **item,
            "featured": True,
            "available": item["slug"] in local,
            "url": f"{SITE_BASE}/{item['slug']}",
        })
    curated_slugs = {i["slug"] for i in CURATED}
    for slug in sorted(local - curated_slugs):
        title, blurb = _readme_meta(PLAYBOOKS_ROOT / slug / "README.md")
        out.append({
            "slug": slug,
            "title": title or slug.replace("-", " ").title(),
            "tag": "",
            "blurb": blurb,
            "featured": False,
            "available": True,
            "url": f"{SITE_BASE}/{slug}",
        })
    return out


def get_runbook_markdown(slug: str) -> str:
    if slug not in _local_slugs():  # doubles as path-traversal guard
        raise KeyError(f"Unknown runbook: {slug}")
    return (PLAYBOOKS_ROOT / slug / "README.md").read_text(encoding="utf-8")
