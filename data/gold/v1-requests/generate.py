"""Render the v1-requests set: every request file, from the committed wording in texts/ and truth.json.

    cd service && uv run python ../data/gold/v1-requests/generate.py            # writes requests/
    cd service && uv run python ../data/gold/v1-requests/generate.py --out DIR  # somewhere else

No model calls and no clock: the same inputs give byte-identical files, which
service/tests/test_request_set.py checks against the committed requests/.

File kinds (the "render" key of a file in truth.json):
  (none)               the text file as it is: WhatsApp exports, voice-note transcripts, call notes
  email                an .eml with From, To, Subject, Date, and PDF attachments rendered from text
  whatsapp_screenshot  a phone screenshot (.png) of a WhatsApp export
  pack                 a full pack from another set, reissued so its files get their own hashes
"""

import argparse
import json
import re
import textwrap
from datetime import datetime
from email.message import EmailMessage
from email.utils import format_datetime
from pathlib import Path

import pymupdf

HERE = Path(__file__).parent
TEXTS = HERE / "texts"


def pdf_from_text(title: str, body: str) -> bytes:
    """A plain A4 document. No creation date or random id, so the bytes are stable."""
    doc = pymupdf.open()
    lines = body.splitlines()
    while lines or doc.page_count == 0:
        page = doc.new_page(width=595, height=842)
        y = 72
        while lines and y < 780:
            line = lines.pop(0)
            for part in textwrap.wrap(line, 90) or [""]:
                page.insert_text((60, y), part, fontname="helv", fontsize=10)
                y += 15
    doc.set_metadata({"title": title, "producer": "bidwright v1-requests", "creator": "", "creationDate": "",
                      "modDate": ""})
    return doc.tobytes(garbage=4, deflate=True, no_new_id=True)


def email(case_id: str, n: int, f: dict, received_at: str) -> bytes:
    msg = EmailMessage()
    msg["From"] = f["from"]
    msg["To"] = f["to"]
    msg["Subject"] = f["subject"]
    msg["Date"] = format_datetime(datetime.fromisoformat(f.get("date", received_at)))
    msg["Message-ID"] = f"<{case_id.lower()}.{n}@v1-requests.example>"
    msg.set_content((TEXTS / f["text"]).read_text())
    for a in f.get("attachments", []):
        msg.add_attachment(pdf_from_text(a["name"], (TEXTS / a["text"]).read_text()), maintype="application",
                           subtype="pdf", filename=a["name"])
    if msg.is_multipart():
        msg.set_boundary(f"==v1-{case_id}-{n}==")
    return msg.as_bytes()


WA_LINE = re.compile(r"^\[(?P<date>[^,]+), (?P<time>\d\d:\d\d):\d\d\] (?P<who>[^:]+): (?P<text>.*)$")


def whatsapp_screenshot(export: str) -> bytes:
    """A 390-point-wide phone screen showing the chat, rendered at 2x."""
    messages = [m.groupdict() for m in map(WA_LINE.match, export.splitlines()) if m]
    width, pad, bubble_w, size, lead = 390, 12, 290, 13, 17
    font = pymupdf.Font("helv")
    wrapped = []
    for m in messages:
        lines, current = [], ""
        for word in m["text"].split():
            trial = f"{current} {word}".strip()
            if font.text_length(trial, size) > bubble_w - 24 and current:
                lines.append(current)
                current = word
            else:
                current = trial
        lines.append(current)
        wrapped.append((m, lines))
    height = 110 + sum(len(lines) * lead + 34 for _, lines in wrapped) + 60
    doc = pymupdf.open()
    page = doc.new_page(width=width, height=height)
    page.draw_rect(page.rect, color=None, fill=(0.925, 0.898, 0.867))
    page.draw_rect(pymupdf.Rect(0, 0, width, 88), color=None, fill=(0.027, 0.369, 0.329))
    page.insert_text((pad, 22), "10:04", fontname="hebo", fontsize=12, color=(1, 1, 1))
    page.insert_text((width - 62, 22), "5G  82%", fontname="helv", fontsize=11, color=(1, 1, 1))
    who = messages[0]["who"] if messages else ""
    page.insert_text((pad + 26, 62), who, fontname="hebo", fontsize=15, color=(1, 1, 1))
    page.insert_text((pad + 26, 78), "online", fontname="helv", fontsize=11, color=(0.85, 0.93, 0.9))
    page.insert_text((pad, 63), "<", fontname="hebo", fontsize=18, color=(1, 1, 1))
    y = 104
    for m, lines in wrapped:
        h = len(lines) * lead + 24
        r = pymupdf.Rect(pad, y, pad + bubble_w, y + h)
        page.draw_rect(r, color=None, fill=(1, 1, 1), radius=0.12)
        for i, line in enumerate(lines):
            page.insert_text((r.x0 + 12, r.y0 + 18 + i * lead), line, fontname="helv", fontsize=size,
                             color=(0.07, 0.07, 0.07))
        page.insert_text((r.x1 - 40, r.y1 - 6), m["time"], fontname="helv", fontsize=9, color=(0.45, 0.45, 0.45))
        y += h + 10
    page.draw_rect(pymupdf.Rect(0, height - 52, width, height), color=None, fill=(0.96, 0.96, 0.96))
    page.draw_rect(pymupdf.Rect(pad, height - 44, width - 56, height - 10), color=None, fill=(1, 1, 1), radius=0.5)
    page.insert_text((pad + 14, height - 22), "Message", fontname="helv", fontsize=13, color=(0.55, 0.55, 0.55))
    return page.get_pixmap(dpi=144).tobytes("png")


def reissue(case_id: str, path: Path) -> bytes:
    """A pack file with a new identity: same content, different bytes, so intake treats it as a new request."""
    data = path.read_bytes()
    if path.suffix.lower() == ".pdf":
        doc = pymupdf.open(stream=data, filetype="pdf")
        doc.set_metadata({**{k: v for k, v in doc.metadata.items() if k in ("title", "author", "subject")},
                          "producer": f"bidwright v1-requests {case_id}", "creationDate": "", "modDate": ""})
        return doc.tobytes(garbage=4, deflate=True, no_new_id=True)
    if path.suffix.lower() == ".eml":
        return f"X-Reissued: v1-requests {case_id}\n".encode() + data
    return data


def render_case(case_id: str, case: dict, out: Path) -> list[str]:
    folder = out / case_id
    folder.mkdir(parents=True, exist_ok=True)
    written = []
    for n, f in enumerate(case["files"], 1):
        if "pack" in f:
            for src in sorted((HERE / f["pack"]).iterdir()):
                if src.is_file():
                    (folder / src.name).write_bytes(reissue(case_id, src))
                    written.append(src.name)
            continue
        render = f.get("render")
        if render == "email":
            data = email(case_id, n, f, case["received_at"])
        elif render == "whatsapp_screenshot":
            data = whatsapp_screenshot((TEXTS / f["text"]).read_text())
        else:
            data = (TEXTS / f["text"]).read_bytes()
        (folder / f["name"]).write_bytes(data)
        written.append(f["name"])
    for f in case.get("followups", []):
        (folder / "followups").mkdir(exist_ok=True)
        (folder / "followups" / f["name"]).write_bytes((TEXTS / f["text"]).read_bytes())
    return written


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=HERE / "requests")
    args = parser.parse_args()
    cases = json.loads((HERE / "truth.json").read_text())["cases"]
    total = 0
    for case_id, case in sorted(cases.items()):
        total += len(render_case(case_id, case, args.out))
    print(f"wrote {total} files for {len(cases)} requests to {args.out}")


if __name__ == "__main__":
    main()
