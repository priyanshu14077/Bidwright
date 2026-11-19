"""Turn a stored file into a rendered PDF and per-page layout lines.

PoC layout reader: PyMuPDF on digital PDFs. Azure AI Document Intelligence
replaces `read_layout` in Phase 1 proper; it returns the same shape (lines
with page coordinates), so nothing downstream changes.
"""

import email
import email.policy
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from bidwright.config import settings

OFFICE_SUFFIXES = {".docx", ".doc", ".xlsx", ".xls", ".pptx", ".odt", ".ods", ".rtf"}
UNSUPPORTED_SUFFIXES = {".dwg", ".dxf", ".rvt", ".ifc", ".skp", ".3dm", ".nwd"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
TEXT_SUFFIXES = {".txt", ".eml"}


@dataclass
class Line:
    line_id: str  # "p{page}:{n}", stable within a document
    text: str
    bbox: list[float]
    start: int | None = None  # character offset in the document's plain text, for text sources


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
    if suffix in TEXT_SUFFIXES:
        return text_to_pdf(file_name, document_text(file_name, data))
    if suffix in OFFICE_SUFFIXES:
        return office_to_pdf(file_name, data)
    return None


def office_to_pdf(file_name: str, data: bytes) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / Path(file_name).name
        src.write_bytes(data)
        subprocess.run(
            [settings.libreoffice, "--headless", f"-env:UserInstallation=file://{tmp}/profile",
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


def document_text(file_name: str, data: bytes) -> str:
    """The plain text of a message, notes file or email (headers, then body), as received."""
    if Path(file_name).suffix.lower() == ".eml":
        headers, body, _ = parse_email(data)
        return "\n".join(f"{k}: {v}" for k, v in headers.items() if v) + "\n\n" + body.strip() + "\n"
    return data.decode("utf-8", errors="replace").replace("\r\n", "\n")


# Text sources are laid out on A4 pages by this one function, for the model (lines with ids) and for the
# rendered PDF alike, so a line's box in the PDF is where its text is drawn.
PAGE_W, PAGE_H, MARGIN, LEADING, SIZE, WRAP_AT = 595, 842, 50, 14, 10, 95


def text_pages(text: str) -> list[Page]:
    """Wrap the text into lines that are exact slices of it, each with its character offset."""
    font = pymupdf.Font("helv")
    slices: list[tuple[int, int]] = []
    offset = 0
    for para in text.split("\n"):
        words = [(m.start() + offset, m.end() + offset) for m in re.finditer(r"\S+", para)]
        line: list[tuple[int, int]] = []
        for w in words:
            if line and w[1] - line[0][0] > WRAP_AT:
                slices.append((line[0][0], line[-1][1]))
                line = []
            line.append(w)
        if line:
            slices.append((line[0][0], line[-1][1]))
        offset += len(para) + 1
    per_page = int((PAGE_H - 2 * MARGIN) / LEADING)
    pages = []
    for p, first in enumerate(range(0, max(len(slices), 1), per_page), 1):
        lines = []
        for n, (a, b) in enumerate(slices[first:first + per_page]):
            y = MARGIN + n * LEADING
            width = font.text_length(text[a:b], SIZE)
            lines.append(Line(f"p{p}:{n + 1}", text[a:b], [MARGIN, round(y - SIZE, 1), round(MARGIN + width, 1),
                                                            round(y + 3, 1)], a))
        pages.append(Page(p, PAGE_W, PAGE_H, lines))
    return pages


def text_to_pdf(title: str, text: str) -> bytes:
    """A readable PDF of a text source. Characters the built-in font lacks (Arabic, Vietnamese tones, emoji)
    do not draw; the app shows text sources as text, so this PDF is only a fallback view."""
    doc = pymupdf.open()
    for page in text_pages(text):
        pdf_page = doc.new_page(width=page.width, height=page.height)
        for line in page.lines:
            pdf_page.insert_text((MARGIN, line.bbox[3] - 3), line.text, fontsize=SIZE, fontname="helv")
    doc.set_metadata({"title": title})
    return doc.tobytes()


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
