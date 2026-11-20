"""Make the sample requests in docs/requests: three levels of scope, each in several kinds of document.

    cd service && uv run python ../docs/requests/make_requests.py

Every client, project and person here is invented. Dates are set around the demo week (Friday 20 November
2026), so deadlines land where the qualification rules can see them; change TODAY to move them all.
"""

from datetime import date, timedelta
from email.message import EmailMessage
from email.utils import format_datetime
from datetime import datetime, timezone, timedelta as td
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font
from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

ROOT = Path(__file__).parent
TODAY = date(2026, 11, 20)  # the Friday the requests arrive
GST = timezone(td(hours=4))
AST = timezone(td(hours=3))


def write(path: str, text: str) -> None:
    p = ROOT / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text.strip() + "\n", encoding="utf-8")


def email(path: str, sender: str, to: str, subject: str, body: str, sent: datetime) -> None:
    m = EmailMessage()
    m["From"], m["To"], m["Subject"], m["Date"] = sender, to, subject, format_datetime(sent)
    m.set_content(body.strip() + "\n")
    p = ROOT / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(bytes(m))


def pdf(path: str, title: str, blocks: list) -> None:
    p = ROOT / path
    p.parent.mkdir(parents=True, exist_ok=True)
    styles = getSampleStyleSheet()
    story = [Paragraph(title, styles["Title"]), Spacer(1, 0.4 * cm)]
    for b in blocks:
        if isinstance(b, tuple) and b[0] == "h":
            story += [Spacer(1, 0.3 * cm), Paragraph(b[1], styles["Heading2"])]
        elif isinstance(b, list):
            t = Table(b, hAlign="LEFT", colWidths=[7 * cm, 8 * cm])
            t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, "#999999"), ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                                   ("BACKGROUND", (0, 0), (-1, 0), "#eeeeee"), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
            story.append(t)
        else:
            story.append(Paragraph(b, styles["BodyText"]))
    SimpleDocTemplate(str(p), pagesize=A4, title=title, leftMargin=2 * cm, rightMargin=2 * cm).build(story)


def sheet(path: str, rows: list[list]) -> None:
    p = ROOT / path
    p.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = "Area schedule"
    for r in rows:
        ws.append(r)
    for c in ws[1]:
        c.font = Font(bold=True)
    ws.column_dimensions["A"].width = 34
    ws.column_dimensions["B"].width = 16
    ws.column_dimensions["C"].width = 30
    wb.save(p)


def chat_screenshot(path: str, title: str, lines: list[tuple[str, str]]) -> None:
    """A phone screenshot of a WhatsApp-style chat: (time, text) bubbles from the client."""
    w, pad = 750, 28
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 30)
        small = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 22)
        bold = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 32)
    except OSError:
        font = small = bold = ImageFont.load_default()
    wrapped = []
    for t, text in lines:
        words, rows, row = text.split(), [], ""
        for word in words:
            trial = f"{row} {word}".strip()
            if font.getlength(trial) > w - 4 * pad - 40:
                rows.append(row)
                row = word
            else:
                row = trial
        rows.append(row)
        wrapped.append((t, rows))
    h = 140 + sum(len(r) * 40 + 70 for _, r in wrapped) + 40
    img = Image.new("RGB", (w, h), "#ece5dd")
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, w, 110], fill="#075e54")
    d.text((pad, 38), title, font=bold, fill="white")
    y = 140
    for t, rows in wrapped:
        bh = len(rows) * 40 + 50
        d.rounded_rectangle([pad, y, w - 3 * pad, y + bh], radius=18, fill="white")
        for i, r in enumerate(rows):
            d.text((pad + 22, y + 14 + i * 40), r, font=font, fill="#111111")
        d.text((w - 3 * pad - 90, y + bh - 32), t, font=small, fill="#777777")
        y += bh + 20
    p = ROOT / path
    p.parent.mkdir(parents=True, exist_ok=True)
    img.save(p)


def wd(n: int) -> date:
    """n working days after TODAY."""
    d, left = TODAY, n
    while left:
        d += timedelta(days=1)
        if d.weekday() < 5:
            left -= 1
    return d


def long(d: date) -> str:
    return f"{d:%A} {d.day} {d:%B %Y}"


# ================================================================ 1. complete scope: expect Go

email("1-complete-scope/A - Email with brief and area schedule/RFP - Wadi Hanifa Offices.eml",
      "Huda Al-Qahtani <h.alqahtani@alrimal-dev.example>", "proposals@northbeam.example",
      "RFP: Wadi Hanifa Offices, Riyadh - design consultancy", f"""
Dear Northbeam Studio,

Al Rimal Real Estate Development Co. invites you to submit a fee proposal for the design of
Wadi Hanifa Offices, a corporate headquarters building in Riyadh, Saudi Arabia.

Scope: architecture and interior design.
Stages: Concept Design, Schematic Design and Design Development.
Gross floor area: 38,400 m². Plot area: 7,200 m².
Interior fit-out area: 11,500 m² (executive floors, lobby and amenity levels).

Please quote your fees in SAR. Our standard terms cap the consultant's liability at 100% of the fee,
and invoices are paid within 45 days.

Submission deadline: {long(TODAY + timedelta(days=24))}, 12:00 Riyadh time.

The project brief and the area schedule are attached.

Kind regards,
Huda Al-Qahtani
Development Manager, Al Rimal Real Estate Development Co.
""", datetime(2026, 11, 20, 9, 12, tzinfo=AST))

pdf("1-complete-scope/A - Email with brief and area schedule/Project brief - Wadi Hanifa Offices.pdf",
    "Wadi Hanifa Offices: Project Brief", [
        "Client: Al Rimal Real Estate Development Co.",
        "Location: King Fahd Road, Riyadh, Kingdom of Saudi Arabia.",
        ("h", "1. The project"),
        "A 14-storey corporate headquarters with two basement levels, a public lobby and a roof garden. "
        "The client will occupy the upper six floors; the remainder is let to tenants.",
        ("h", "2. Areas"),
        [["Item", "Area"], ["Plot area", "7,200 m²"], ["Gross floor area (above ground)", "38,400 m²"],
         ["Interior fit-out (client floors, lobby, amenities)", "11,500 m²"], ["Basement parking", "16,800 m² (not in GFA)"]],
        ("h", "3. Services and stages"),
        "Architecture and interior design, from Concept Design through Schematic Design to Design Development. "
        "A local architect of record will produce construction documents; their appointment is separate.",
        ("h", "4. Commercial terms"),
        "Fees in Saudi riyals (SAR). Liability capped at 100% of the consultant's fee. Payment within 45 days of invoice.",
        ("h", "5. Programme"),
        f"Proposals are due by 12:00 on {long(TODAY + timedelta(days=24))}. Appointment is expected in January 2027.",
    ])

sheet("1-complete-scope/A - Email with brief and area schedule/Area schedule - Wadi Hanifa Offices.xlsx", [
    ["Level", "Area (m²)", "Use"],
    ["Ground floor", 3400, "Lobby, retail, café"],
    ["Levels 1-7", 17500, "Tenant offices"],
    ["Levels 8-13", 15300, "Client headquarters"],
    ["Roof", 2200, "Amenities and roof garden"],
    ["Total GFA", 38400, ""],
    ["Plot", 7200, ""],
    ["Interior fit-out", 11500, "Client floors, lobby, amenities"],
])

write("1-complete-scope/B - WhatsApp, repeat client/WhatsApp Chat - Marina Crest.txt", f"""
[19/11/2026, 16:02:41] Rania - Marina Crest: Hi team, Rania from Marina Crest Developments again 👋
[19/11/2026, 16:03:10] Rania - Marina Crest: After Bay Square went well we have a residential tower in Al Barsha South, Dubai
[19/11/2026, 16:03:55] Rania - Marina Crest: G+28, about 52,000 m² GFA on a 4,800 m² plot
[19/11/2026, 16:04:30] Rania - Marina Crest: We need architecture and landscape, concept and schematic design only
[19/11/2026, 16:05:02] Rania - Marina Crest: Landscape area is roughly 2,900 m² (podium + ground)
[19/11/2026, 16:05:40] Rania - Marina Crest: Fees in AED please, same terms as last time (liability capped at the fee)
[19/11/2026, 16:06:15] Rania - Marina Crest: Could you send the proposal by {long(TODAY + timedelta(days=14))}?
""")

write("1-complete-scope/C - Call notes, hotel/Call notes - Corniche hotel Doha.txt", f"""
Call notes, {long(TODAY - timedelta(days=1))}, 11:30 (Doha time)
With: Faris Al-Mannai, Qamar Hospitality Holding
Taken by: Demo Estimator

- Project: Qamar Corniche Hotel, West Bay, Doha, Qatar. Five-star hotel, 220 keys plus serviced apartments.
- Gross floor area 46,000 m² on a 9,000 m² site.
- They want architecture and interior design. Interior fit-out area 18,000 m² (rooms, F&B, ballroom, spa).
- Stages: Concept Design through Design Development. A local firm will do construction documents.
- Fees in QAR. Liability capped at 100% of fee, payment 30 days.
- Proposal due {long(TODAY + timedelta(days=28))}.
- Faris will email the operator's brand standards next week; not needed for the fee.
""")

# ================================================================ 2. partial scope: expect Clarify

write("2-partial-scope/A - WhatsApp, rough ask/WhatsApp Chat - Lusail offices.txt", """
[20/11/2026, 08:41:05] Omar Haddad: Morning, quick one
[20/11/2026, 08:41:30] Omar Haddad: We're looking at a mid-rise office building in Lusail, roughly 25k sqm
[20/11/2026, 08:42:12] Omar Haddad: Just need concept for now, maybe more later
[20/11/2026, 08:42:50] Omar Haddad: Can you give us a number by Thursday next week?
""")

write("2-partial-scope/A - WhatsApp, rough ask/followups/WhatsApp reply - Lusail offices.txt", """
[20/11/2026, 11:05:12] Omar Haddad: Hi again, answers to your questions:
[20/11/2026, 11:05:40] Omar Haddad: The company is Al Waab Real Estate Development
[20/11/2026, 11:06:02] Omar Haddad: Site is 4,200 m², GFA 25,000 m² as I said
[20/11/2026, 11:06:30] Omar Haddad: Fees in QAR is fine. Quality: high-end, Grade A offices
""")

email("2-partial-scope/B - Email, missing areas/Fee request - Al Mouj villas Muscat.eml",
      "Salim Al-Busaidi <salim@sablestone.example>", "info@northbeam.example",
      "Fee request: villa community, Muscat", """
Hello,

Sablestone Properties is planning a gated villa community in Al Mouj, Muscat, Oman.
The site is about 6 hectares and we are thinking of around 40 villas with a clubhouse.

Could you let us know your fee for the design? We would like the fee in USD.

We have not fixed the floor areas yet; the master developer's guidelines are still being finalised.

Thanks,
Salim Al-Busaidi
Sablestone Properties
""", datetime(2026, 11, 20, 10, 5, tzinfo=GST))

chat_screenshot("2-partial-scope/C - Screenshot and notes, unclear area/Screenshot 2026-11-20 at 09.15.png", "Yusuf (Seef Bay)", [
    ("09:02", "Hi, this is Yusuf from Seef Bay Developments, Manama"),
    ("09:03", "Mixed-use block in Seef: retail podium plus two residential towers"),
    ("09:04", "GFA is 30,000 m² in the feasibility study"),
    ("09:05", "Architecture, concept to schematic. Fees in BHD please"),
])
write("2-partial-scope/C - Screenshot and notes, unclear area/Call notes - Seef Bay follow-up.txt", f"""
Call notes, {long(TODAY)}, 14:10
With: Yusuf Al-Khalifa, Seef Bay Developments (Manama, Bahrain)

- Follow-up on his WhatsApp this morning.
- He is not sure whether the 30,000 m² in the feasibility study includes the podium car park. If it does,
  the building itself is nearer 24,000 m². He will check with their cost consultant and come back.
- Plot is 5,600 m².
- Their standard contract has unlimited liability for the consultant; he says this is "usually negotiable".
- Wants the proposal by {long(TODAY + timedelta(days=21))}.
""")

# ================================================================ 3. thin or out of scope: expect Clarify or Decline

write("3-thin-or-out-of-scope/A - WhatsApp, one line/WhatsApp Chat - +971 55 301 7720.txt", """
[20/11/2026, 19:12:44] +971 55 301 7720: Hi, what do you charge for designing a building? thx
""")

email("3-thin-or-out-of-scope/B - Email, market not served/Invitation - Ancoats museum Manchester.eml",
      "Eleanor Firth <e.firth@ancoats-heritage.example>", "studio@northbeam.example",
      "Invitation to tender: Ancoats Textile Museum, Manchester", f"""
Dear Northbeam Studio,

The Ancoats Heritage Trust invites you to tender for architectural services for a new textile museum
in Manchester, United Kingdom: 6,800 m² GFA on a 3,100 m² site, RIBA Stages 2 to 4.

Fees in GBP. Tenders are due by {long(TODAY + timedelta(days=30))}.

Best regards,
Eleanor Firth
Ancoats Heritage Trust
""", datetime(2026, 11, 20, 11, 30, tzinfo=timezone.utc))

email("3-thin-or-out-of-scope/C - Email, service we do not offer/RFQ - Mussafah warehouse engineering.eml",
      "Procurement <rfq@gulfstore-logistics.example>", "info@northbeam.example",
      "RFQ: structural and MEP engineering, Mussafah warehouse", f"""
Dear Sir/Madam,

GulfStore Logistics requests a quotation for structural engineering and MEP engineering design only
for a 24,000 m² warehouse in Mussafah, Abu Dhabi. Architecture is already appointed.

Quotations in AED by {long(TODAY + timedelta(days=12))}.

Regards,
Procurement, GulfStore Logistics LLC
""", datetime(2026, 11, 20, 8, 0, tzinfo=GST))

email("3-thin-or-out-of-scope/D - Email, deadline too close/Competition - Corniche offices Jeddah.eml",
      "Majed Al-Harbi <majed@redsea-office.example>", "proposals@northbeam.example",
      "Design competition: Corniche office tower, Jeddah", f"""
Dear Northbeam Studio,

Red Sea Office Partners invite you to a design competition for a 31,000 m² office tower on the
Corniche in Jeddah, Saudi Arabia (site 5,200 m²): architecture, concept design.

Fees in SAR. Submissions close on {long(wd(1))} at 14:00.

Regards,
Majed Al-Harbi
""", datetime(2026, 11, 20, 13, 45, tzinfo=AST))


# ================================================================ more kinds of request (ten more)

def word(path: str, title: str, paragraphs: list[str]) -> None:
    """A Word document, e.g. meeting minutes."""
    from docx import Document
    doc = Document()
    doc.add_heading(title, level=1)
    for para in paragraphs:
        if para.startswith("- "):
            doc.add_paragraph(para[2:], style="List Bullet")
        else:
            doc.add_paragraph(para)
    p = ROOT / path
    p.parent.mkdir(parents=True, exist_ok=True)
    doc.save(p)


ICT = timezone(td(hours=7))
SGT = timezone(td(hours=8))
CET = timezone(td(hours=1))

# 1. complete scope ------------------------------------------------------------------------------

email("1-complete-scope/D - Email, masterplan/RFP - Thu Thiem riverside masterplan.eml",
      "Nguyen Thi Lan <lan.nguyen@saigonbay-land.example>", "proposals@northbeam.example",
      "RFP: Thu Thiem riverside masterplan, Ho Chi Minh City", f"""
Dear Northbeam Studio,

Saigon Bay Land JSC invites your proposal for the masterplan of a 42-hectare riverside site in Thu Thiem,
Ho Chi Minh City, Vietnam: mixed residential, office and retail districts with a riverfront park.

Scope: masterplan only, from Feasibility through Concept Design to Schematic Design.
Site area: 42 ha. No building design in this appointment.

Fees in USD. Liability capped at 100% of the fee; payment within 30 days.
Proposals are due by {long(TODAY + timedelta(days=21))}, 17:00 Ho Chi Minh City time.

Best regards,
Nguyen Thi Lan
Head of Development, Saigon Bay Land JSC
""", datetime(2026, 11, 20, 9, 40, tzinfo=ICT))

email("1-complete-scope/E - Email and Excel, interior fit-out only/Fit-out RFP - Marina One office floors.eml",
      "Rachel Lim <rachel.lim@harbourfront-cap.example>", "studio@northbeam.example",
      "Interior design RFP: Marina One office floors, Singapore", f"""
Hi Northbeam team,

Harbourfront Capital is fitting out four office floors at Marina One, Singapore, for its regional headquarters.

We need interior design only (the base building is complete): Concept Design, Schematic Design and Design
Development. The fit-out area is 6,800 m² over four floors of 9,200 m² GFA; the area schedule is attached.

Fees in SGD please. Liability capped at the fee, payment in 30 days.
Please send your proposal by {long(TODAY + timedelta(days=18))}.

Thanks,
Rachel Lim
Workplace Director, Harbourfront Capital
""", datetime(2026, 11, 20, 10, 15, tzinfo=SGT))
sheet("1-complete-scope/E - Email and Excel, interior fit-out only/Area schedule - Marina One floors.xlsx", [
    ["Floor", "Fit-out area (m²)", "Use"],
    ["Level 31", 1700, "Reception, client lounge, meeting suite"],
    ["Level 32", 1700, "Open office"],
    ["Level 33", 1700, "Open office, focus rooms"],
    ["Level 34", 1700, "Executive floor, boardroom"],
    ["Total fit-out", 6800, ""],
    ["GFA of the four floors", 9200, ""],
])

email("1-complete-scope/F - Email in Spanish, hotel and landscape/Solicitud de propuesta - Hotel Poblenou.eml",
      "Marta Puig <mpuig@grupmediterrani.example>", "barcelona@northbeam.example",
      "Solicitud de propuesta: Hotel Poblenou, Barcelona", f"""
Estimados señores:

Grup Mediterrani Hotels les invita a presentar su propuesta de honorarios para el Hotel Poblenou en Barcelona,
España: un hotel de cuatro estrellas de 180 habitaciones.

Superficie construida: 14.500 m². Parcela: 3.200 m². Superficie de paisajismo: 2.100 m² (patio y cubierta).
Servicios: arquitectura y paisajismo. Fases: Anteproyecto y Proyecto Básico.

Honorarios en euros (EUR). Responsabilidad limitada al importe de los honorarios; pago a 60 días.
Plazo de entrega de la propuesta: {TODAY + timedelta(days=25):%d/%m/%Y}.

Atentamente,
Marta Puig
Directora de Desarrollo, Grup Mediterrani Hotels
""", datetime(2026, 11, 20, 9, 5, tzinfo=CET))

print("made", sum(1 for p in ROOT.rglob("*") if p.is_file() and p.suffix in (".txt", ".eml", ".pdf", ".xlsx", ".png", ".docx")), "files")
