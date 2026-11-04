"""Generate the preloaded sample document for the Docs Q&A demo.

A fact sheet about the device itself — so the RAG demo doubles as a
product pitch: 'ask the machine about the machine.'
"""

from pathlib import Path

from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

OUT = Path(__file__).resolve().parent.parent / "docs" / "dell-pro-max-gb10-overview.pdf"

SECTIONS = [
    ("Dell Pro Max with GB10 — Product Overview",
     "The Dell Pro Max with GB10 is a compact desktop AI supercomputer built around the "
     "NVIDIA GB10 Grace Blackwell Superchip. It brings data-center-class AI capability to a "
     "desk-side form factor, letting teams prototype, fine-tune, and run inference on large "
     "AI models locally — without sending data to the cloud."),
    ("Superchip architecture",
     "The NVIDIA GB10 Grace Blackwell Superchip combines an NVIDIA Blackwell-generation GPU "
     "with a 20-core Arm CPU (10 Cortex-X925 performance cores and 10 Cortex-A725 efficiency "
     "cores) connected via NVLink-C2C chip-to-chip interconnect. This delivers up to "
     "1 petaFLOP of AI performance at FP4 precision."),
    ("Unified memory",
     "The system features 128 GB of unified LPDDR5x memory shared coherently between the CPU "
     "and GPU. Unlike traditional PCs where GPU memory is a separate, small pool, the GB10's "
     "unified architecture lets AI models up to roughly 200 billion parameters fit in memory "
     "on a single device. Two units can be connected to work with models up to about "
     "405 billion parameters."),
    ("Storage and connectivity",
     "The Dell Pro Max with GB10 ships with up to 4 TB of NVMe SSD storage. Connectivity "
     "includes NVIDIA ConnectX-7 SmartNIC networking for clustering two units, Wi-Fi 7, "
     "10 GbE, and USB4 ports. The system runs NVIDIA DGX OS, an Ubuntu Linux–based operating "
     "system with the full NVIDIA AI software stack preinstalled."),
    ("AI software stack",
     "The device supports the complete NVIDIA AI platform, including CUDA 13, TensorRT-LLM, "
     "NeMo, RAPIDS, and popular open-source frameworks such as PyTorch, Ollama, vLLM, and "
     "llama.cpp. NVIDIA publishes step-by-step playbooks covering inference, fine-tuning, "
     "image generation, data science, and multi-agent applications."),
    ("Why local AI matters",
     "Running AI workloads locally keeps sensitive data on-premises, eliminates per-token "
     "cloud API costs, and provides consistent performance without network latency. For "
     "regulated industries — healthcare, legal, finance, government — local AI enables the "
     "use of large language models on confidential data that cannot leave the organization."),
    ("Power and form factor",
     "The system draws approximately 240 watts under full load — comparable to a gaming "
     "laptop — and fits in a 150 mm × 150 mm × 50.5 mm chassis. It plugs into a standard "
     "wall outlet, requires no special cooling, and operates quietly enough for an office "
     "or conference room."),
]


def main() -> None:
    OUT.parent.mkdir(exist_ok=True)
    doc = SimpleDocTemplate(str(OUT), pagesize=letter,
                            topMargin=0.9 * inch, bottomMargin=0.9 * inch)
    styles = getSampleStyleSheet()
    h = ParagraphStyle("H", parent=styles["Heading2"], spaceBefore=18)
    body = ParagraphStyle("B", parent=styles["BodyText"], fontSize=11, leading=16)
    flow = [Paragraph(SECTIONS[0][0], styles["Title"]), Spacer(1, 6),
            Paragraph(SECTIONS[0][1], body)]
    for title, text in SECTIONS[1:]:
        flow += [Paragraph(title, h), Paragraph(text, body)]
    doc.build(flow)
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
