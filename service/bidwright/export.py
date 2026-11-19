"""Export: an approved proposal version as a Word document and a PDF.

    approved version ──> context ──> docxtpl fills the template ──> .docx ──> LibreOffice ──> .pdf
                                       (prose: Markdown → Word)                    │
                         blob store <──────────────────────────────────────────────┘
                         proposal.output (format, sha256, uri, template) + a proposal.event

Only frozen versions (approved or superseded) are exported, so a file always
matches a version that can no longer change. Exporting the same version with the
same template again returns the files already made.
"""

import hashlib
import io
import re
import threading
from datetime import date

from docx.shared import Pt
from docxtpl import DocxTemplate
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy import Connection, text

from bidwright import proposals
from bidwright.auth import require
from bidwright.config import REPO_ROOT
from bidwright.db import engine
from bidwright.intake import reading
from bidwright.storage import blob_store
from bidwright.tenancy import Principal

DEFAULT_TEMPLATE = REPO_ROOT / "data" / "templates" / "proposal-v0.docx"
# The two documents a proposal can be exported as: the full proposal, and a short fee letter for quick asks.
TEMPLATES = {"full": DEFAULT_TEMPLATE, "letter": REPO_ROOT / "data" / "templates" / "fee-letter-v0.docx"}
TEMPLATE_NAMES = {"full": "Full proposal", "letter": "Fee letter"}
MIME = {"docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "pdf": "application/pdf"}
_one_conversion_at_a_time = threading.Lock()  # LibreOffice is memory-hungry; the server is small

router = APIRouter(prefix="/api")
READ, APPROVE = Depends(require("read")), Depends(require("approve"))


# ---------------------------------------------------------------- Markdown → Word

def add_runs(paragraph, text: str) -> None:
    for part in re.split(r"(\*\*[^*]+\*\*)", text):
        if part.startswith("**") and part.endswith("**"):
            paragraph.add_run(part[2:-2]).bold = True
        elif part:
            paragraph.add_run(part)


def markdown_subdoc(tpl: DocxTemplate, md: str):
    """The Markdown the drafting step writes, as Word paragraphs in the template's own styles."""
    sd = tpl.new_subdoc()
    for block in re.split(r"\n{2,}", (md or "").replace("\r\n", "\n").strip()):
        lines = [ln for ln in block.split("\n") if ln.strip()]
        if not lines:
            continue
        heading = re.match(r"^#{2,4}\s+(.*)$", lines[0])
        if heading and len(lines) == 1:
            sd.add_paragraph(heading.group(1), style="Heading 2")
        elif all(ln.strip().startswith("|") for ln in lines):
            rows = [[c.strip() for c in ln.strip().strip("|").split("|")] for ln in lines
                    if not re.match(r"^\|\s*-", ln.strip())]
            table = sd.add_table(rows=len(rows), cols=max(len(r) for r in rows))
            table.style = "Table Grid"
            for i, row in enumerate(rows):
                for j, value in enumerate(row):
                    cell = table.cell(i, j)
                    cell.text = ""
                    add_runs(cell.paragraphs[0], value)
                    if i == 0:
                        for run in cell.paragraphs[0].runs:
                            run.bold = True
            sd.add_paragraph().paragraph_format.space_after = Pt(2)
        elif all(ln.startswith("- ") for ln in lines):
            for ln in lines:
                add_runs(sd.add_paragraph(style="List Bullet"), ln[2:])
        else:
            p = sd.add_paragraph()
            for i, ln in enumerate(lines):
                if i:
                    p.add_run().add_break()
                add_runs(p, ln)
    return sd


# ---------------------------------------------------------------- the document

def amount(n) -> str:
    return f"{float(n):,.0f}"


def context(conn: Connection, tpl: DocxTemplate, v: dict) -> dict:
    labels = dict(conn.execute(text("SELECT code, label FROM ref.service UNION ALL SELECT code, label FROM ref.stage")).all())
    firm = conn.execute(text("SELECT o.name FROM tenancy.organization o JOIN proposal.bid b USING (org_id) "
                             "WHERE b.proposal_id = :p"), dict(p=v["proposal_id"])).scalar_one()
    studio_code = (v["estimate"].get("inputs") or {}).get("studio")
    studio = conn.execute(text("SELECT name FROM ref.studio WHERE code = :c"), dict(c=studio_code)).scalar() if studio_code else None
    sections = {s["code"]: s for s in v["sections"]}
    cover = sections["cover"]["data"]
    return {
        "firm": firm, "studio": studio or "", "reference": v["reference"], "version": v["version_no"],
        "date": date.fromisoformat(str(v["approved_at"])[:10]).strftime("%-d %B %Y"),
        "client": cover.get("client") or "", "project": cover.get("project") or v["title"], "location": cover.get("location") or "",
        "client_reference": cover.get("client_reference") or "", "validity_days": cover.get("validity_days", 90),
        "currency": v["currency"],
        "payment_days": cover.get("payment_days") or _payment_days(conn),  # older versions predate the snapshot
        "understanding": markdown_subdoc(tpl, sections["understanding"]["body"]),
        "scope": markdown_subdoc(tpl, sections["scope"]["body"]),
        "exclusions": markdown_subdoc(tpl, sections["exclusions"]["body"]),
        "terms": markdown_subdoc(tpl, sections["terms"]["body"]),
        "deliverables": sections["deliverables"]["data"] or [],
        "programme": sections["programme"]["data"] or [],
        "fees": [dict(service=labels.get(l["service"], l["service"]), stage=labels.get(l["stage"], l["stage"]),
                      amount=amount(l["amount_local"])) for l in v["fee_lines"]],
        "fee_total": amount(v["fee_total_local"]),
        "reimbursables": [dict(item=labels.get(r["type"], r["type"].replace("_", " ").capitalize()), qty=r["qty"],
                               rate=amount(r["unit_rate_local"]), amount=amount(r["amount_local"]), note=r["note"])
                          for r in sections["reimbursables"]["data"] or []],
        "payment": [dict(seq=m["seq"], milestone=m["milestone"], pct=f"{m['pct']:g}", amount=amount(m["amount_local"]))
                    for m in sections["payment"]["data"] or []],
    }


def _payment_days(conn: Connection) -> int:
    return int(conn.execute(text("SELECT value FROM ref.fee_assumption WHERE key = 'terms.payment_days'")).scalar() or 30)


def template_id(data: bytes) -> str:
    return f"default:{hashlib.sha256(data).hexdigest()[:12]}"


def render(conn: Connection, v: dict, template: bytes) -> bytes:
    tpl = DocxTemplate(io.BytesIO(template))
    tpl.render(context(conn, tpl, v), autoescape=True)
    out = io.BytesIO()
    tpl.save(out)
    return out.getvalue()


def file_stem(v: dict) -> str:
    project = re.sub(r"[^A-Za-z0-9]+", "-", v["title"]).strip("-")[:50]
    return f"{v['reference']}_v{v['version_no']}_{project}"


# ---------------------------------------------------------------- endpoints

@router.post("/proposals/{proposal_id}/versions/{version_no}/export")
def export(proposal_id: int, version_no: int, who: Principal = APPROVE):
    template = DEFAULT_TEMPLATE.read_bytes()
    tid = template_id(template)
    with engine.connect() as conn:
        v = proposals.load_version(conn, proposal_id, version_no)
        if v["status"] == "draft":
            raise HTTPException(409, "only an approved version can be exported; approve it first")
        existing = [o for o in v["outputs"] if o["template"] == tid]
        if {o["format"] for o in existing} == {"docx", "pdf"}:
            return {"outputs": existing, "reused": True}
        docx = render(conn, v, template)
    stem = file_stem(v)
    with _one_conversion_at_a_time:
        pdf = reading.office_to_pdf(f"{stem}.docx", docx)
    files = {"docx": docx, "pdf": pdf}
    with engine.begin() as conn:
        for fmt, data in files.items():
            sha, uri = blob_store.put(data, f"{stem}.{fmt}")
            conn.execute(text("""INSERT INTO proposal.output (version_id, format, blob_sha, blob_uri, size_bytes, file_name,
                                   template, created_by) VALUES (:v, :f, :sha, :uri, :n, :name, :t, :who)"""),
                         dict(v=v["version_id"], f=fmt, sha=sha, uri=uri, n=len(data), name=f"{stem}.{fmt}", t=tid,
                              who=who.email))
        proposals.log(conn, proposal_id, v["version_id"], "export", who, None, None,
                      {"files": [f"{stem}.{f}" for f in files], "template": tid})
        return {"outputs": proposals.outputs(conn, v["version_id"]), "reused": False}


@router.get("/outputs/{output_id}")
def download(output_id: int, _: Principal = READ):
    with engine.connect() as conn:
        o = conn.execute(text("SELECT format, blob_uri, file_name FROM proposal.output WHERE output_id = :o"),
                         dict(o=output_id)).first()
    if o is None:
        raise HTTPException(404)
    return Response(blob_store.get(o.blob_uri), media_type=MIME[o.format],
                    headers={"Content-Disposition": f'attachment; filename="{o.file_name}"'})
