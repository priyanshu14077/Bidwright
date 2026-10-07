"""Generate one synthetic RFP pack per archive proposal, plus the gold truth.

Each pack is what a client would have sent SOG before SOG wrote that proposal:
a covering email, the main RFP, an area schedule and the client's terms.
The request-side fields of the proposal are the ground truth.

Traps planted on purpose, so accuracy is measured on something harder than
SOG's own template:
- three unrelated layouts (formal tender, developer brief, scope matrix)
- regional stage names and "BUA" for GFA; sq ft with Indian digit grouping
- site areas in hectares for large sites
- distractor areas (earlier phases, excluded parking)
- deadline stated only in the email for some packs
- client names written differently from the archive
- engineering services requested from others, and stages done by others
- a brief vs area schedule GFA conflict in CONFLICT_PACKS
- area schedule as .xlsx in XLSX_PACKS

    uv run --group dev python tools/gen_rfp_packs.py
"""

import json
import random
import shutil
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import format_datetime
from pathlib import Path

from openpyxl import Workbook
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

REPO = Path(__file__).resolve().parents[2]
DATASET = "v0-synthetic"
ARCHIVE = REPO / "data" / "archive" / DATASET / "sog_synthetic_archive.json"
OUT = REPO / "data" / "gold" / DATASET

SQFT_PER_M2 = 10.7639104
CONFLICT_PACKS = {"P02": 1.11, "P08": 0.9, "P11": 1.08, "P14": 1.15}
XLSX_PACKS = {"P05", "P09"}
DEADLINE_ONLY_IN_EMAIL = {"P01", "P04", "P07", "P10", "P13"}

COUNTRY = {"UAE": "AE", "KSA": "SA", "Spain": "ES", "Singapore": "SG", "Vietnam": "VN", "China": "CN", "India": "IN"}
TZ = {"AE": (4, "GST"), "SA": (3, "AST"), "ES": (2, "CEST"), "SG": (8, "SGT"), "VN": (7, "ICT"),
      "CN": (8, "CST"), "IN": (5.5, "IST")}

STAGE_DEFAULT = {
    "feasibility": "Feasibility Study",
    "concept": "Concept Design",
    "schematic_design": "Schematic Design",
    "design_development": "Design Development",
    "gfc_review": "Review of GFC drawings",
    "construction_review": "Design review during construction",
}
STAGE_REGIONAL = {
    "ES": {"schematic_design": "Anteproyecto", "design_development": "Proyecto Básico"},
    "CN": {"schematic_design": "Scheme Design", "design_development": "Preliminary Design"},
    "VN": {"design_development": "Basic Design"},
    "SA": {"concept": "RIBA Stage 2 - Concept Design", "schematic_design": "RIBA Stage 3 - Spatial Coordination",
           "design_development": "RIBA Stage 4 - Technical Design"},
    "SG": {"construction_review": "Construction Stage Design Review"},
    "IN": {"schematic_design": "Preliminary Drawings"},
}
SERVICE_TEXT = {
    "architecture": "architectural design",
    "interior": "interior design",
    "landscape": "landscape architecture",
    "masterplan": "masterplanning",
}
CLIENT_AS_WRITTEN = {
    "P01": "Marina Crest Developments",
    "P14": "Marina Crest Developments L.L.C.",
    "P10": "Han River Resorts Joint Stock Company",
    "P06": "Costa Luz Hoteles",
    "P08": "Harbourline Residences",
}
SPECIAL_REQUESTS = {
    "P02": ["Unlimited design revisions at concept stage", "Lighting design concept for public areas"],
    "P01": ["Interior design of the off-plan sales gallery"],
    "P11": ["Support for LEED Gold certification"],
    "P09": ["Height-limit negotiation support with the authorities"],
    "P13": ["3D walkthrough animation for marketing"],
}
OUT_OF_SCOPE_TAIL = "Construction documentation and tender documents will be prepared by the Local Architect of Record and are not part of this appointment."

styles = getSampleStyleSheet()
BODY = ParagraphStyle("body", parent=styles["BodyText"], fontSize=10, leading=13)
SMALL = ParagraphStyle("small", parent=BODY, fontSize=8.5, leading=11)
H1 = ParagraphStyle("h1", parent=styles["Heading1"], fontSize=17, spaceAfter=8)
H2 = ParagraphStyle("h2", parent=styles["Heading2"], fontSize=12.5, spaceBefore=10, spaceAfter=4)
TITLE = ParagraphStyle("title", parent=H1, fontSize=22, leading=26, spaceBefore=60)


def group_indian(n: float) -> str:
    s = str(int(round(n)))
    if len(s) <= 3:
        return s
    head, tail = s[:-3], s[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return ",".join(parts + [tail])


class Areas:
    """Writes areas the way the project's market writes them."""

    def __init__(self, country: str):
        self.india = country == "IN"

    def fmt(self, m2: float, allow_ha: bool = False) -> str:
        if self.india:
            return f"{group_indian(m2 * SQFT_PER_M2)} sq ft"
        if allow_ha and m2 >= 50000:
            return f"{m2 / 10000:.1f} ha".replace(".0 ha", " ha")
        return f"{m2:,.0f} m²"

    def unit(self) -> str:
        return "sq ft" if self.india else "m²"

    def num(self, m2: float) -> str:
        return group_indian(m2 * SQFT_PER_M2) if self.india else f"{m2:,.0f}"


def stage_labels(p: dict, country: str) -> dict[str, str]:
    labels = dict(STAGE_DEFAULT)
    labels.update(STAGE_REGIONAL.get(country, {}))
    labels.update(p["document_variations"].get("stage_terms", {}))
    return labels


def deadline(p: dict, country: str) -> datetime:
    hours, _ = TZ[country]
    d = date.fromisoformat(p["submission_date"])
    return datetime(d.year, d.month, d.day, 14, 0, tzinfo=timezone(timedelta(hours=hours)))


def area_mix(p: dict, rng: random.Random) -> list[tuple[str, float]]:
    gfa = p["gfa_m2"]
    t = p["typology"]
    if t == "residential":
        parts = [("Residential apartments", 0.86), ("Amenities and clubhouse", 0.06), ("Lobbies and common areas", 0.08)]
        if "villa" in p["cost_type"]:
            parts = [("Villas", 0.9), ("Community centre", 0.04), ("Retail and F&B", 0.06)]
    elif t == "hospitality":
        parts = [("Guest rooms and suites", 0.62), ("F&B and lobby", 0.14), ("Spa and wellness", 0.09), ("Back of house", 0.15)]
    elif t == "office":
        parts = [("Office floors (NLA zones)", 0.82), ("Ground floor lobby and retail", 0.08), ("Core and plant", 0.10)]
    elif t == "mixed_use":
        parts = [("Residential", 0.55), ("Retail podium", 0.2), ("Office", 0.15), ("Hotel / serviced apartments", 0.10)]
    else:
        return []
    jitter = [w * rng.uniform(0.92, 1.08) for _, w in parts]
    scale = gfa / sum(jitter)
    rows = [[name, round(w * scale / 10) * 10] for (name, _), w in zip(parts, jitter)]
    rows[-1][1] += gfa - sum(r[1] for r in rows)
    return [(n, v) for n, v in rows]


def table(rows: list[list[str]], widths: list[float], header: bool = True) -> Table:
    t = Table([[Paragraph(str(c), SMALL) for c in r] for r in rows], colWidths=[w * mm for w in widths])
    style = [("GRID", (0, 0), (-1, -1), 0.4, colors.grey), ("VALIGN", (0, 0), (-1, -1), "TOP")]
    if header:
        style.append(("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8e8e8")))
    t.setStyle(TableStyle(style))
    return t


def build_pdf(path: Path, story: list, title: str) -> None:
    doc = SimpleDocTemplate(str(path), pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm,
                            topMargin=18 * mm, bottomMargin=18 * mm, title=title)
    doc.build(story)


class Pack:
    def __init__(self, p: dict, client: dict, rng: random.Random):
        self.p = p
        self.rng = rng
        self.pid = p["proposal_id"]
        self.country = COUNTRY[p["country"]]
        self.areas = Areas(self.country)
        self.labels = stage_labels(p, self.country)
        self.client_written = CLIENT_AS_WRITTEN.get(self.pid, p["client_name"])
        self.deadline = deadline(p, self.country)
        self.issued = self.deadline - timedelta(days=21, hours=5)
        self.rfp_ref = f"{''.join(w[0] for w in self.client_written.split()[:3]).upper()}-RFP-{self.issued:%Y}-{rng.randint(10, 99)}"
        self.project = p["title"].split(" - ")[0]
        self.brief_gfa = p["gfa_m2"] * CONFLICT_PACKS.get(self.pid, 1.0)
        self.services = [s for s in ["masterplan", "architecture", "landscape", "interior"] if s in p["services"]]
        self.style = ["tender", "brief", "matrix"][int(self.pid[1:]) % 3]

    # ---------------------------------------------------------------- text helpers
    def services_sentence(self) -> str:
        names = [SERVICE_TEXT[s] for s in self.services]
        text = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
        return text

    def stages_sentence(self) -> str:
        names = [self.labels[s] for s in self.p["stage_package"]]
        return "; ".join(names)

    def location(self) -> str:
        return f"{self.p['city']}, {self.p['country'] if self.p['country'] not in ('UAE', 'KSA') else {'UAE': 'United Arab Emirates', 'KSA': 'Kingdom of Saudi Arabia'}[self.p['country']]}"

    def gfa_label(self) -> str:
        return "Built-Up Area (BUA)" if self.country in ("AE", "SA") else "Gross Floor Area (GFA)"

    def deadline_text(self) -> str:
        _, abbr = TZ[self.country]
        return f"14:00 {abbr} on {self.deadline:%A %d %B %Y}"

    def distractor(self) -> str | None:
        g = self.p["gfa_m2"]
        options = [
            f"Basement car parking of approximately {self.areas.fmt(g * 0.22)} is excluded from the {self.gfa_label()} figures.",
            f"Phase 1 of the wider development, completed in {self.issued.year - 4}, comprised {self.areas.fmt(g * 0.7)} and is not part of this appointment.",
        ]
        return self.rng.choice(options) if self.p["typology"] != "masterplan" else None

    def key_facts(self, brief: bool) -> list[list[str]]:
        p, a = self.p, self.areas
        rows = [["Item", "Detail"], ["Project", self.project], ["Location", self.location()],
                ["Client", self.client_written]]
        if p["typology"] != "masterplan":
            gfa = self.brief_gfa if brief else p["gfa_m2"]
            approx = "approximately " if brief and self.pid in CONFLICT_PACKS else ""
            rows.append([self.gfa_label(), approx + a.fmt(gfa)])
        rows.append(["Site / plot area", a.fmt(p["site_area_m2"], allow_ha=True)])
        if p["units"]:
            rows.append(["Residential units", f"{p['units']:,}"])
        if p["keys"]:
            rows.append(["Hotel keys", f"{p['keys']:,}"])
        return rows

    def scope_paragraphs(self) -> list:
        p, a = self.p, self.areas
        out = [Paragraph(f"The Client invites proposals for {self.services_sentence()} services for the project.", BODY)]
        if "interior" in self.services and p["fitout_area_m2"]:
            out.append(Paragraph(
                f"The interior design scope covers lobbies, amenities, public areas and typical units, "
                f"with a total interior design area of {a.fmt(p['fitout_area_m2'])}.", BODY))
        if "landscape" in self.services and p["landscape_area_m2"]:
            out.append(Paragraph(f"Landscape works cover approximately {a.fmt(p['landscape_area_m2'], allow_ha=True)} "
                                 f"of hard and soft landscape.", BODY))
        if "masterplan" in self.services:
            out.append(Paragraph(f"The masterplan covers the full site of {a.fmt(p['site_area_m2'], allow_ha=True)}.", BODY))
        out.append(Paragraph(f"Design stages required: {self.stages_sentence()}.", BODY))
        if "design_development" in p["stage_package"] or "gfc_review" in p["stage_package"]:
            out.append(Paragraph(OUT_OF_SCOPE_TAIL, BODY))
        out.append(Paragraph("Structural, MEP and civil engineering consultants will be appointed separately by the Client.", BODY))
        return out

    def special_requests(self) -> list:
        reqs = SPECIAL_REQUESTS.get(self.pid, [])
        if not reqs:
            return []
        return [Paragraph("Additional requirements", H2)] + [Paragraph(f"• {r}", BODY) for r in reqs]

    def submission_paragraph(self) -> Paragraph:
        if self.pid in DEADLINE_ONLY_IN_EMAIL:
            return Paragraph("Proposals must be submitted by the date and time stated in the covering invitation email.", BODY)
        return Paragraph(f"Proposals must be received no later than {self.deadline_text()}.", BODY)

    # ---------------------------------------------------------------- documents
    def main_rfp(self, path: Path) -> None:
        p = self.p
        story: list = []
        if self.style == "tender":
            story += [Paragraph(f"{self.client_written}", BODY), Paragraph("REQUEST FOR PROPOSAL", TITLE),
                      Paragraph(f"Design Consultancy Services: {self.project}", H1),
                      Paragraph(f"Tender reference: {self.rfp_ref}<br/>Date of issue: {self.issued:%d %B %Y}", BODY),
                      PageBreak(),
                      Paragraph("Section 1. Instructions to Tenderers", H2),
                      Paragraph("1.1 Tenderers shall submit separate technical and commercial proposals.", BODY),
                      Paragraph("1.2 " + self.submission_paragraph().text, BODY),
                      Paragraph("1.3 Fees shall be quoted as lump sums per stage, exclusive of VAT.", BODY),
                      Paragraph("Section 2. Terms of Reference", H2),
                      Paragraph(f"2.1 The project is located in {self.location()}. {p['complexity_notes']}.", BODY)]
            if p["typology"] != "masterplan":
                story.append(Paragraph(
                    f"2.2 The development comprises a {self.gfa_label()} of {self.areas.fmt(self.brief_gfa)} "
                    f"on a site of {self.areas.fmt(p['site_area_m2'], allow_ha=True)}.", BODY))
            if d := self.distractor():
                story.append(Paragraph("2.3 " + d, BODY))
            story += [Paragraph("Section 3. Scope of Services", H2), *self.scope_paragraphs(), *self.special_requests()]
        elif self.style == "brief":
            story += [Paragraph(f"{self.project}", TITLE), Paragraph("Architectural Design Brief", H1),
                      Paragraph(f"Issued by {self.client_written} · {self.issued:%d %b %Y} · Ref {self.rfp_ref}", BODY),
                      Spacer(1, 8),
                      Paragraph("About the project", H2),
                      Paragraph(f"{self.client_written} is developing {p['title'].split(' - ')[-1]} in {self.location()}. "
                                f"{p['complexity_notes']}.", BODY),
                      table(self.key_facts(brief=True), [55, 115])]
            if d := self.distractor():
                story.append(Paragraph(d, BODY))
            story += [Paragraph("What we need from the design team", H2), *self.scope_paragraphs(),
                      *self.special_requests(), Paragraph("Submission", H2), self.submission_paragraph()]
        else:
            stage_codes = p["stage_package"]
            header = ["Service"] + [self.labels[s] for s in stage_codes]
            rows = [header]
            for s in ["masterplan", "architecture", "landscape", "interior"]:
                rows.append([SERVICE_TEXT[s].capitalize()] + ["Required" if s in self.services else "-" for _ in stage_codes])
            rows.append(["MEP / structural engineering"] + ["By others" for _ in stage_codes])
            story += [Paragraph("TERMS OF REFERENCE", TITLE), Paragraph(self.project, H1),
                      Paragraph(f"Client: {self.client_written}<br/>Reference: {self.rfp_ref}<br/>Issued: {self.issued:%Y-%m-%d}", BODY),
                      Paragraph("A. Project data", H2), table(self.key_facts(brief=True), [55, 115]),
                      Paragraph("B. Scope matrix", H2),
                      table(rows, [50] + [120 / len(stage_codes)] * len(stage_codes)),
                      Paragraph("C. Notes", H2), *self.scope_paragraphs()[1:], *self.special_requests(),
                      Paragraph("D. Submission", H2), self.submission_paragraph()]
        build_pdf(path, story, f"RFP {self.project}")

    def area_schedule_rows(self) -> list[list[str]]:
        a = self.areas
        rows = [["Component", f"Area ({a.unit()})"]]
        mix = area_mix(self.p, self.rng)
        for name, v in mix:
            rows.append([name, a.num(v)])
        if mix:
            rows.append([f"Total {self.gfa_label()}", a.num(self.p["gfa_m2"])])
        rows.append(["Site area", a.num(self.p["site_area_m2"])])
        if self.p["landscape_area_m2"]:
            rows.append(["Landscape area", a.num(self.p["landscape_area_m2"])])
        return rows

    def area_schedule(self, folder: Path) -> str:
        rows = self.area_schedule_rows()
        if self.pid in XLSX_PACKS:
            wb = Workbook()
            ws = wb.active
            ws.title = "Area schedule"
            ws.append([f"{self.project} - Area schedule (rev B)"])
            ws.append([])
            for r in rows:
                ws.append(r)
            name = "Area_Schedule_revB.xlsx"
            wb.save(folder / name)
            return name
        name = "Area_Schedule.pdf"
        build_pdf(folder / name, [Paragraph(f"{self.project}", H1), Paragraph("Schedule of Areas - Revision B", H2),
                                  table(rows, [100, 60])], "Area schedule")
        return name

    def client_terms(self, folder: Path) -> None:
        devs = {d["clause"]: d["client_request"] for d in self.p["tc_deviations"]}
        liability = ("The Consultant's liability under this Agreement shall be unlimited."
                     if devs.get("Liability cap") == "Uncapped liability"
                     else "The Consultant's aggregate liability shall not exceed the total fee.")
        pay_days = 90 if devs.get("Payment terms") == "90 days from invoice" else 30
        clauses = [
            ("1. Payment", f"Invoices shall be paid within {pay_days} days of receipt of a valid invoice."),
            ("2. Liability", liability),
            ("3. Insurance", "The Consultant shall maintain professional indemnity insurance throughout the services."),
            ("4. Copyright", "Copyright in all designs shall transfer to the Client upon full payment."),
            ("5. Termination", "The Client may terminate the Agreement on 30 days' written notice."),
        ]
        if "Design rounds" in devs:
            clauses.append(("6. Revisions", "The Consultant shall carry out unlimited design revisions at concept stage."))
        story = [Paragraph("Conditions of Appointment", H1), Paragraph(f"{self.client_written} - {self.rfp_ref}", BODY)]
        for h, body in clauses:
            story += [Paragraph(h, H2), Paragraph(body, BODY)]
        build_pdf(folder / "Client_Terms.pdf", story, "Conditions of appointment")
        self.liability = "uncapped" if "unlimited" in liability else "capped"
        self.payment_days = pay_days

    def email(self, folder: Path, attachments: list[str]) -> None:
        msg = EmailMessage()
        msg["From"] = f"Procurement <tenders@{self.client_written.split()[0].lower()}.example>"
        msg["To"] = "rfp@sogdesign.example"
        msg["Subject"] = f"Invitation to tender - {self.project} ({self.rfp_ref})"
        msg["Date"] = format_datetime(self.issued)
        msg.set_content(
            f"Dear SOG Design team,\n\n"
            f"{self.client_written} is pleased to invite you to submit a proposal for design consultancy services "
            f"for {self.project}, {self.p['city']}.\n\n"
            f"Please find attached: {', '.join(attachments)}.\n\n"
            f"The deadline for submission is {self.deadline_text()}. Clarification questions may be sent to this address "
            f"until {(self.deadline - timedelta(days=7)):%d %B %Y}.\n\n"
            f"Kind regards,\nProcurement Team\n{self.client_written}\n"
        )
        (folder / "Invitation_email.eml").write_bytes(bytes(msg))

    def truth(self) -> dict:
        p = self.p
        fields = {
            "client_name": p["client_name"],
            "client_status": p["client_status"],
            "country": self.country,
            "city": p["city"],
            "typology": p["typology"],
            "services": sorted({"interior_design" if s == "interior" else s for s in p["services"]}),
            "stage_package": p["stage_package"],
            "gfa_m2": p["gfa_m2"] if p["typology"] != "masterplan" else None,
            "site_area_m2": p["site_area_m2"],
            "fitout_area_m2": p["fitout_area_m2"] if "interior" in p["services"] else None,
            "landscape_area_m2": p["landscape_area_m2"] if "landscape" in p["services"] else None,
            "units": p["units"],
            "keys": p["keys"],
            "submission_deadline": self.deadline.isoformat(),
            "liability_cap": self.liability,
            "payment_terms_days": self.payment_days,
        }
        conflicts = []
        if self.pid in CONFLICT_PACKS:
            conflicts.append({"field": "gfa_m2", "values": [p["gfa_m2"], round(self.brief_gfa)],
                              "documents": ["area_schedule", "main_rfp"]})
        return {
            "proposal_id": self.pid,
            "pack": f"packs/{self.pid}",
            "style": self.style,
            "issued_at": self.issued.isoformat(),
            "fields": fields,
            "conflicts": conflicts,
            "special_requests": SPECIAL_REQUESTS.get(self.pid, []),
            "traps": [t for t, on in [
                ("sqft_indian_grouping", self.country == "IN"),
                ("hectares", p["site_area_m2"] >= 50000 and self.country != "IN"),
                ("bua_label", self.country in ("AE", "SA")),
                ("regional_stage_names", any(self.labels[s] != STAGE_DEFAULT[s] for s in p["stage_package"])),
                ("deadline_only_in_email", self.pid in DEADLINE_ONLY_IN_EMAIL),
                ("client_name_variant", self.pid in CLIENT_AS_WRITTEN),
                ("gfa_conflict", self.pid in CONFLICT_PACKS),
                ("xlsx_area_schedule", self.pid in XLSX_PACKS),
            ] if on],
        }

    def write(self, folder: Path) -> dict:
        folder.mkdir(parents=True)
        self.main_rfp(folder / "RFP_Main.pdf")
        schedule = self.area_schedule(folder)
        self.client_terms(folder)
        self.email(folder, ["RFP_Main.pdf", schedule, "Client_Terms.pdf"])
        return self.truth()


def main() -> None:
    data = json.loads(ARCHIVE.read_text())
    clients = {c["client_id"]: c for c in data["clients"]}
    packs_dir = OUT / "packs"
    if packs_dir.exists():
        shutil.rmtree(packs_dir)
    truth = {}
    for p in data["proposals"]:
        rng = random.Random(p["proposal_id"])
        truth[p["proposal_id"]] = Pack(p, clients[p["client_id"]], rng).write(packs_dir / p["proposal_id"])
    (OUT / "truth.json").write_text(json.dumps(
        {"dataset_version": DATASET, "generated_by": "service/tools/gen_rfp_packs.py", "cases": truth},
        indent=1, ensure_ascii=False))
    print(f"wrote {len(truth)} packs to {packs_dir}")


if __name__ == "__main__":
    main()
