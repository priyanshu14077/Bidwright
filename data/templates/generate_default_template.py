"""Generate the default proposal template, data/templates/proposal-v0.docx.

The layout follows the sample archive's proposals: a cover block, nine numbered
sections, a running header and a page-numbered footer. It is an ordinary Word
file with docxtpl (Jinja) placeholders, so a practice can restyle it in Word and
upload its own (step 6) as long as the placeholders stay.

Placeholders the export fills (bidwright/export.py):
  firm, studio, reference, version, date, client, project, location, client_reference,
  validity_days, currency, payment_days, fee_total
  understanding, scope, exclusions, terms     prose sections, inserted with {{p name }}
  deliverables[] item, qty
  programme[]    label, duration
  fees[]         service, stage, amount
  reimbursables[] item, qty, rate, amount, note
  payment[]      seq, milestone, pct, amount

    cd service && uv run python ../data/templates/generate_default_template.py
"""

from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

OUT = Path(__file__).parent / "proposal-v0.docx"
INK, GREY = RGBColor(0x1E, 0x22, 0x26), RGBColor(0x59, 0x61, 0x6A)


def styles(doc: Document) -> None:
    normal = doc.styles["Normal"]
    normal.font.name, normal.font.size, normal.font.color.rgb = "Arial", Pt(10), INK
    normal.paragraph_format.space_after = Pt(6)
    for name, size, before in (("Title", 22, 0), ("Heading 1", 14, 18), ("Heading 2", 11, 10)):
        s = doc.styles[name]
        s.font.name, s.font.size, s.font.bold, s.font.color.rgb = "Arial", Pt(size), True, INK
        s.paragraph_format.space_before, s.paragraph_format.space_after = Pt(before), Pt(6)
        s.paragraph_format.keep_with_next = True
        rpr = s.element.get_or_add_rPr()  # headings must not fall back to the theme font
        fonts = rpr.find(qn("w:rFonts"))
        if fonts is None:
            fonts = OxmlElement("w:rFonts")
            rpr.append(fonts)
        for attr in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
            fonts.set(qn(attr), "Arial")
        for attr in ("w:asciiTheme", "w:hAnsiTheme", "w:cstheme", "w:eastAsiaTheme"):
            fonts.attrib.pop(qn(attr), None)


def page_field(paragraph) -> None:
    run = paragraph.add_run()
    for kind, text in (("begin", None), (None, "PAGE"), ("end", None)):
        if kind:
            el = OxmlElement("w:fldChar")
            el.set(qn("w:fldCharType"), kind)
        else:
            el = OxmlElement("w:instrText")
            el.set(qn("xml:space"), "preserve")
            el.text = text
        run._r.append(el)


def table(doc: Document, head: list[str], loop: str, cells: list[str], total: list[str] | None = None,
          widths: list[float] | None = None) -> None:
    """Header row, then a row repeated for every item in loop ({%tr for %} ... {%tr endfor %})."""
    rows = [head, [f"{{%tr for r in {loop} %}}"] + [""] * (len(head) - 1), cells,
            ["{%tr endfor %}"] + [""] * (len(head) - 1)] + ([total] if total else [])
    t = doc.add_table(rows=len(rows), cols=len(head))
    t.style = "Table Grid"
    for i, row in enumerate(rows):
        for j, value in enumerate(row):
            cell = t.cell(i, j)
            cell.text = value
            run = cell.paragraphs[0].runs[0] if cell.paragraphs[0].runs else None
            if run and (i == 0 or (total and i == len(rows) - 1)):
                run.bold = True
            if j > 0 and j == len(head) - 1 and i != 1 and i != 3:
                cell.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.RIGHT
            if widths:
                cell.width = Cm(widths[j])
    if widths:  # Word reads the cell widths; LibreOffice reads the table grid
        t.autofit = False
        for col, w in zip(t._tbl.tblGrid.findall(qn("w:gridCol")), widths):
            col.set(qn("w:w"), str(Cm(w).twips))
    doc.add_paragraph()


def build() -> Document:
    doc = Document()
    styles(doc)
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21), Cm(29.7)
    sec.left_margin = sec.right_margin = Cm(2)
    sec.top_margin, sec.bottom_margin = Cm(2.2), Cm(2)
    head = sec.header.paragraphs[0]
    head.text = "{{ firm }}  ·  {{ reference }}"
    head.runs[0].font.size, head.runs[0].font.color.rgb = Pt(8), GREY
    foot = sec.footer.paragraphs[0]
    foot.text = "{{ reference }} version {{ version }}  ·  {{ date }}  ·  Page "
    foot.runs[0].font.size, foot.runs[0].font.color.rgb = Pt(8), GREY
    page_field(foot)

    firm = doc.add_paragraph()
    firm.add_run("{{ firm }}").bold = True
    doc.add_paragraph("{{ studio }}").runs[0].font.color.rgb = GREY
    doc.add_paragraph("Fee Proposal", style="Title")
    p = doc.add_paragraph()
    p.add_run("{{ project }}").bold = True
    for line in ("Prepared for: {{ client }}", "Location: {{ location }}", "Reference: {{ reference }}",
                 "{% if client_reference %}Your reference: {{ client_reference }}{% endif %}", "Date: {{ date }}",
                 "Validity: {{ validity_days }} days from the date of issue"):
        doc.add_paragraph(line).paragraph_format.space_after = Pt(2)

    doc.add_paragraph("1. Project Understanding", style="Heading 1")
    doc.add_paragraph("{{p understanding }}")
    doc.add_paragraph("2. Scope of Services", style="Heading 1")
    doc.add_paragraph("{{p scope }}")
    doc.add_paragraph("3. Deliverables", style="Heading 1")
    doc.add_paragraph("{%p for d in deliverables %}")
    doc.add_paragraph("{{ d.item }}: {{ d.qty }}", style="List Bullet")
    doc.add_paragraph("{%p endfor %}")
    doc.add_paragraph("4. Programme", style="Heading 1")
    table(doc, ["Stage", "Indicative duration"], "programme", ["{{ r.label }}", "{{ r.duration }}"], widths=[9, 8])
    doc.add_paragraph("Durations exclude client review periods of two weeks per stage.")
    doc.add_paragraph("5. Fee Proposal", style="Heading 1")
    doc.add_paragraph("All fees are in {{ currency }} and exclude VAT or other applicable taxes.")
    table(doc, ["Service", "Stage", "Fee ({{ currency }})"], "fees", ["{{ r.service }}", "{{ r.stage }}", "{{ r.amount }}"],
          total=["", "Total professional fee", "{{ fee_total }}"], widths=[6, 6, 5])
    doc.add_paragraph("6. Reimbursable Expenses", style="Heading 1")
    table(doc, ["Item", "Quantity", "Unit rate ({{ currency }})", "Amount ({{ currency }})"], "reimbursables",
          ["{{ r.item }}", "{{ r.qty }}", "{{ r.rate }}", "{{ r.amount }}"], widths=[7, 3, 3.5, 3.5])
    doc.add_paragraph("{%p for r in reimbursables %}")
    doc.add_paragraph("{{ r.item }}: {{ r.note }}").runs[0].font.color.rgb = GREY
    doc.add_paragraph("{%p endfor %}")
    doc.add_paragraph("7. Payment Schedule", style="Heading 1")
    table(doc, ["No.", "Milestone", "Share", "Amount ({{ currency }})"], "payment",
          ["{{ r.seq }}", "{{ r.milestone }}", "{{ r.pct }}%", "{{ r.amount }}"], widths=[1.5, 9, 2.5, 4])
    doc.add_paragraph("Invoices are payable within {{ payment_days }} days of issue.")
    doc.add_paragraph("8. Exclusions", style="Heading 1")
    doc.add_paragraph("{{p exclusions }}")
    doc.add_paragraph("9. Terms and Conditions", style="Heading 1")
    doc.add_paragraph("{{p terms }}")
    return doc


if __name__ == "__main__":
    build().save(OUT)
    print(f"wrote {OUT.name}")
