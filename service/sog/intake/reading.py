"""Turn a stored file into a rendered PDF and per-page layout lines.

PoC layout reader: PyMuPDF on digital PDFs. Azure AI Document Intelligence
replaces `read_layout` in Phase 1 proper; it returns the same shape (lines
with page coordinates), so nothing downstream changes.
"""

import email
import email.policy
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from sog.config import settings

OFFICE_SUFFIXES = {".docx", ".doc", ".xlsx", ".xls", ".pptx", ".odt", ".ods", ".rtf"}
UNSUPPORTED_SUFFIXES = {".dwg", ".dxf", ".rvt", ".ifc", ".skp", ".3dm", ".nwd"}


@dataclass
class Line:
    line_id: str  # "p{page}:{n}", stable within a document
    text: str
    bbox: list[float]


@dataclass
class Page:
    page_no: int
    width: float
    height: float
    lines: list[Line]


def is_supported(file_name: str) -> bool:
    return Path(file_name).suffix.lower() not in UNSUPPORTED_SUFFIXES


def render_pdf(file_name: str, data: bytes) -> bytes | None:
    """PDF bytes for any readable file, or None if we can't read it in Phase 1."""
    suffix = Path(file_name).suffix.lower()
    if suffix == ".pdf":
        return data
    if suffix == ".eml":
        return email_to_pdf(data)
    if suffix == ".txt":
        return text_to_pdf(file_name, data.decode("utf-8", errors="replace"))
    if suffix in OFFICE_SUFFIXES:
        return office_to_pdf(file_name, data)
    return None


def office_to_pdf(file_name: str, data: bytes) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / Path(file_name).name
        src.write_bytes(data)
        subprocess.run(
            [settings.sog_libreoffice, "--headless", f"-env:UserInstallation=file://{tmp}/profile",
             "--convert-to", "pdf", "--outdir", tmp, str(src)],
            check=True, capture_output=True, timeout=120,
        )
        return (Path(tmp) / (src.stem + ".pdf")).read_bytes()


def parse_email(data: bytes) -> tuple[dict[str, str], str, list[tuple[str, bytes]]]:
    """Headers, plain-text body and attachments of an .eml file."""
    msg = email.message_from_bytes(data, policy=email.policy.default)
    headers = {k: str(msg.get(k, "")) for k in ("From", "To", "Date", "Subject")}
    body_part = msg.get_body(preferencelist=("plain", "html"))
    body = body_part.get_content() if body_part else ""
    attachments = [(part.get_filename(), part.get_content()) for part in msg.iter_attachments() if part.get_filename()]
    attachments = [(n, c if isinstance(c, bytes) else c.encode()) for n, c in attachments]
    return headers, body, attachments


def email_to_pdf(data: bytes) -> bytes:
    headers, body, _ = parse_email(data)
    head = "\n".join(f"{k}: {v}" for k, v in headers.items())
    return text_to_pdf("email", f"{head}\n\n{body}")


def text_to_pdf(title: str, text: str) -> bytes:
    doc = pymupdf.open()
    width, height, margin = 595, 842, 50
    lines = []
    for para in text.splitlines():
        lines.extend(wrap(para, 95) or [""])
    per_page = int((height - 2 * margin) / 14)
    for start in range(0, max(len(lines), 1), per_page):
        page = doc.new_page(width=width, height=height)
        y = margin
        for line in lines[start:start + per_page]:
            page.insert_text((margin, y), line, fontsize=10, fontname="helv")
            y += 14
    return doc.tobytes()


def wrap(text: str, width: int) -> list[str]:
    out, current = [], ""
    for word in text.split():
        if current and len(current) + 1 + len(word) > width:
            out.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        out.append(current)
    return out


def read_layout(pdf: bytes) -> list[Page]:
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    pages = []
    for page in doc:
        lines: list[Line] = []
        for block in page.get_text("dict")["blocks"]:
            for raw in block.get("lines", []):
                text = "".join(span["text"] for span in raw["spans"]).strip()
                if text:
                    lines.append(Line(f"p{page.number + 1}:{len(lines) + 1}", text, [round(v, 1) for v in raw["bbox"]]))
        pages.append(Page(page.number + 1, page.rect.width, page.rect.height, lines))
    return pages


def search_quote(pdf: bytes, page_no: int, quote: str) -> list[list[float]]:
    """Rectangles where the quote appears on the page; empty if not found verbatim."""
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    page = doc[page_no - 1]
    return [[round(r.x0, 1), round(r.y0, 1), round(r.x1, 1), round(r.y1, 1)] for r in page.search_for(quote)]
