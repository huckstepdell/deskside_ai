"""Generate the fictional sample documents for the vertical use-case demos.

Both documents are OBVIOUSLY synthetic (fictional city, names, and numbers,
labeled as training samples) so nothing in a customer room can be mistaken
for real PHI or a real case. Dropped into docs/, they are auto-ingested by
Docs Q&A at startup.
"""

from pathlib import Path

from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

DOCS = Path(__file__).resolve().parent.parent / "docs"

HOSPITAL = [
    ("Springfield General Hospital — Clinical Documentation Policy (FICTIONAL SAMPLE)",
     "This is a fictional sample document for AI demonstration purposes only. It does not "
     "represent any real institution, patient, or policy. Springfield General Hospital is an "
     "invented facility."),
    ("Discharge summary requirements",
     "Every discharge summary must be completed within 48 hours of discharge and include: "
     "admitting and discharge diagnoses, a hospital-course narrative in plain language, "
     "medication reconciliation listing all changes with reasons, pending laboratory results "
     "with the responsible follow-up provider, and scheduled follow-up appointments. Summaries "
     "for patients discharged to skilled nursing facilities must additionally include functional "
     "status and dietary requirements."),
    ("Prior authorization workflow",
     "Prior authorization requests must cite the specific plan criteria being met, attach "
     "relevant clinical notes from the preceding 90 days, and document at least one "
     "guideline-concordant conservative treatment attempted, where clinically appropriate. "
     "Requests for advanced imaging (MRI, CT, PET) require documentation of the clinical "
     "question the study will answer. Urgent requests are flagged when a delay would "
     "jeopardize life, limb, or eyesight, and must be decided within 72 hours."),
    ("Documentation turnaround standards",
     "Clinic visit notes must be signed within 72 hours. Operative notes require a brief "
     "note immediately post-procedure and a full note within 24 hours. Verbal orders must "
     "be countersigned within 48 hours. Departments falling below 90 percent on-time "
     "completion for two consecutive months receive documentation support resources."),
    ("Protected health information handling",
     "Patient data may not be entered into external cloud services that are not covered by "
     "a business associate agreement. AI tools used for documentation assistance must run "
     "on hospital-controlled infrastructure or under an executed BAA. De-identified data "
     "used for quality improvement must meet Safe Harbor requirements: removal of all 18 "
     "HIPAA identifiers."),
    ("Coding and billing documentation",
     "Evaluation and management levels must be supported by either medical decision making "
     "or total time. Time-based billing requires a statement of total time and a summary of "
     "activities. Split or shared visits must identify the substantive portion performed by "
     "the billing provider."),
]

POLICE = [
    ("Springfield Police Department — Incident Report 26-04187 (FICTIONAL TRAINING SAMPLE)",
     "This is a fictional training document for AI demonstration purposes only. All names, "
     "addresses, and events are invented. Any resemblance to real persons or cases is "
     "coincidental."),
    ("Incident overview",
     "On the evening of March 14, officers responded to a reported break-in at Hartwell "
     "Electronics, 412 Commerce Street. The rear service door showed pry damage near the "
     "latch. The interior stockroom was disturbed, with two shelving units emptied of "
     "high-value inventory: portable GPUs, laptops, and networking equipment with an "
     "estimated value of $48,200."),
    ("Scene observations",
     "A shoe impression was recovered from dust near the rear entry, size estimated 10 to 11, "
     "with a distinctive hexagonal tread. A blue fiber sample was collected from the damaged "
     "door frame. The alarm panel showed a disarm event at 21:47 using a shared staff code "
     "that has been in circulation for over two years. Exterior camera coverage was obscured; "
     "the parking-lot camera captured a light-colored panel van at 21:32 with an obscured "
     "plate, dwell time 26 minutes."),
    ("Witness statements",
     "A neighboring business owner, Dana Okafor, reported hearing metallic banging between "
     "21:30 and 21:45 and observed a van matching the camera description parked against the "
     "loading dock. A former employee, interviewed as a routine contact, stated the staff "
     "disarm code was widely known and had been shared in a group chat. The store manager, "
     "Renee Calloway, provided an inventory export and noted the missing items were "
     "restocked only two days earlier, following a similar unsolved incident at the "
     "Fairview location last month."),
    ("Evidence log",
     "Item E-1: shoe impression cast, rear stockroom. Item E-2: blue fiber sample, door "
     "frame. Item E-3: alarm panel event export. Item E-4: parking-lot camera footage, "
     "21:00 to 22:30. Item E-5: inventory discrepancy report. All items logged under "
     "chain-of-custody procedure 7-B."),
    ("Open questions for follow-up",
     "Whether the Fairview incident shares the same entry method and vehicle description; "
     "who had current knowledge of the restock schedule; whether the shared disarm code "
     "narrows the suspect pool or effectively widens it; and whether traffic cameras on "
     "Commerce Street captured the van's approach route."),
]


def build(path: Path, sections: list[tuple[str, str]]) -> None:
    doc = SimpleDocTemplate(str(path), pagesize=letter,
                            topMargin=0.9 * inch, bottomMargin=0.9 * inch)
    styles = getSampleStyleSheet()
    h = ParagraphStyle("H", parent=styles["Heading2"], spaceBefore=18)
    body = ParagraphStyle("B", parent=styles["BodyText"], fontSize=11, leading=16)
    flow = [Paragraph(sections[0][0], styles["Title"]), Spacer(1, 6),
            Paragraph(sections[0][1], body)]
    for title, text in sections[1:]:
        flow += [Paragraph(title, h), Paragraph(text, body)]
    doc.build(flow)
    print(f"Wrote {path}")


if __name__ == "__main__":
    DOCS.mkdir(exist_ok=True)
    build(DOCS / "springfield-general-clinical-policy-SAMPLE.pdf", HOSPITAL)
    build(DOCS / "springfield-pd-incident-26-04187-SAMPLE.pdf", POLICE)
