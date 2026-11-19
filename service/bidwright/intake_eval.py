"""Score intake against the v1-requests gold set: does Bidwright see what a request says, what it leaves out,
and what to do next?

    uv run python -m bidwright.intake_eval                  # every request (model calls: a few cents each)
    uv run python -m bidwright.intake_eval --cases W01 F01  # some
    uv run python -m bidwright.intake_eval --rescore        # score the last run's envelopes again, no model calls

For each request and each scored field, the truth says found, ambiguous, missing or referenced, and the system
says found, ambiguous, estimated or missing. The measures:

- status accuracy: the system's status matches. "Estimated" is right for a missing field (the engine filled the
  gap and said so); found or estimated are both right for a referenced one.
- gaps caught: of the fields the request leaves missing or ambiguous, how many the system did not present as found.
- false gaps: of the fields the request does state, how many the system presented as missing, ambiguous or
  estimated (it would ask the client for something already given).
- value accuracy: of the fields stated and shown as found or estimated, how many have the right value.
- recommendation accuracy: go, clarify or no-go matches.
- question coverage: of the fields the follow-up questions must cover, how many they do.

Writes data/gold/v1-requests/runs/<timestamp>.md and an eval.run row.
"""

import argparse
import contextvars
import json
import re
from pathlib import Path

import pymupdf
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from rapidfuzz import fuzz
from sqlalchemy import Connection, text

from bidwright.archive.loader import REQUEST_SET
from bidwright.config import REPO_ROOT, settings
from bidwright.db import engine, owner_engine, workspace
from bidwright.engine import ENGINE_VERSION, LEGAL_SUFFIXES
from bidwright.extraction import PROMPT_VERSION, extract_envelope
from bidwright.intake.ingest import delete_envelope, ingest

SET_DIR = REPO_ROOT / "data" / "gold" / REQUEST_SET
FIELDS = ["client_name", "client_status", "country", "city", "typology", "services", "stage_package", "gfa_m2",
          "site_area_m2", "quality_tier", "billing_currency", "submission_deadline"]
LEAD = {"qualified": "go", "needs_info": "clarify", "recommend_no_bid": "no-go"}
GAP = {"missing", "ambiguous"}
STATED = {"found", "referenced"}


# ---------------------------------------------------------------- what the system says

def predict(conn: Connection, envelope_id: int) -> dict:
    """The system's view of one request, from its gate 1 assessment: a status and value per scored field, the
    recommendation, the rules that fired (flags left out) and the fields its follow-up questions ask about."""
    a = conn.execute(text("SELECT assessment FROM intake.assessment WHERE envelope_id = :e"),
                     dict(e=envelope_id)).scalar()
    if a is None:
        return {"fields": {n: {"status": "missing", "value": None} for n in FIELDS}, "recommendation": None,
                "rules": [], "questions_cover": []}
    by_name = {f["name"]: f for f in a["fields"]}
    fields = {n: {"status": by_name[n]["status"], "value": by_name[n]["value"]} if n in by_name
              else {"status": "missing", "value": None} for n in FIELDS}
    return {"fields": fields, "recommendation": a["recommendation"],
            "rules": sorted(r["name"] for r in a["rules"] if r["outcome"] != "flag"),
            "questions_cover": questions_cover(conn, envelope_id, a)}


def questions_cover(conn: Connection, envelope_id: int, a: dict) -> list[str]:
    """Fields the worded follow-up questions ask about (the assessment's list if they were not worded)."""
    worded = conn.execute(text("SELECT fields FROM intake.followup WHERE envelope_id = :e"),
                          dict(e=envelope_id)).scalar()
    return list(worded) if worded is not None else a["ask"]


# ---------------------------------------------------------------- scoring

def clean(name: str) -> str:
    return re.sub(r"\s+", " ", re.sub(LEGAL_SUFFIXES, "", (name or "").lower())).strip(" .,")


def same_value(field: str, truth, got) -> bool:
    if truth in (None, [], "") or got in (None, [], ""):
        return truth in (None, [], "") and got in (None, [], "")
    if field.endswith("_m2"):
        return abs(float(got) - float(truth)) <= 0.02 * float(truth)
    if field in ("services", "stage_package"):
        return set(got) == set(truth)
    if field == "client_name":
        return fuzz.token_set_ratio(clean(got), clean(truth)) >= 90
    if field == "submission_deadline":
        return str(got)[:10] == str(truth)[:10]
    return str(got).casefold() == str(truth).casefold()


def status_ok(truth: str, got: str) -> bool:
    if truth == "referenced":
        return got in ("found", "estimated")
    if truth == "missing":
        return got in ("missing", "estimated")
    return got == truth


def score(case: dict, envelope_id: int, seen: dict) -> dict:
    fields = {}
    for name in FIELDS:
        t, g = case["fields"][name], seen["fields"][name]
        fields[name] = {
            "truth": t["status"], "truth_value": t["value"], "hint": t.get("hint"),
            "got": g["status"], "got_value": g["value"],
            "status_ok": status_ok(t["status"], g["status"]),
            "gap": t["status"] in GAP, "caught": t["status"] in GAP and g["status"] != "found",
            "false_gap": t["status"] in STATED and g["status"] in ("missing", "ambiguous")
                         or (t["status"] == "found" and g["status"] == "estimated"),
            "value_ok": (same_value(name, t["value"], g["value"])
                         if t["status"] in STATED and g["status"] in ("found", "estimated") else None),
        }
    expected = case["expected"]
    want_q = set(expected["questions_cover"])
    return {
        "case_id": case["case_id"], "kind": case["kind"], "channel": case["channel"], "envelope_id": envelope_id,
        "fields": fields,
        "recommendation": {"expected": expected["recommendation"], "got": seen["recommendation"],
                           "ok": expected["recommendation"] == seen["recommendation"]},
        "rules": {"expected": sorted(expected["rules"]), "got": seen["rules"],
                  "missed": sorted(set(expected["rules"]) - set(seen["rules"])),
                  "extra": sorted(set(seen["rules"]) - set(expected["rules"]))},
        "questions": {"expected": sorted(want_q), "covered": sorted(want_q & set(seen["questions_cover"]))},
    }


def ratio(n: int, d: int) -> float | None:
    return round(n / d, 4) if d else None


def summarise(results: list[dict]) -> dict:
    cells = [f | {"field": n, "kind": r["kind"]} for r in results for n, f in r["fields"].items()]

    def status_acc(pick):
        sel = [c for c in cells if pick(c)]
        return ratio(sum(c["status_ok"] for c in sel), len(sel))

    gaps = [c for c in cells if c["gap"]]
    stated = [c for c in cells if c["truth"] in STATED]
    valued = [c for c in cells if c["value_ok"] is not None]
    q_want = sum(len(r["questions"]["expected"]) for r in results)
    confusion: dict[str, dict[str, int]] = {}
    for r in results:
        e, g = r["recommendation"]["expected"], r["recommendation"]["got"] or "none"
        confusion.setdefault(e, {}).setdefault(g, 0)
        confusion[e][g] += 1
    return {
        "requests": len(results),
        "status_accuracy": status_acc(lambda c: True),
        "gaps_caught": f"{sum(c['caught'] for c in gaps)}/{len(gaps)}",
        "gap_recall": ratio(sum(c["caught"] for c in gaps), len(gaps)),
        "false_gaps": f"{sum(c['false_gap'] for c in stated)}/{len(stated)}",
        "value_accuracy": ratio(sum(c["value_ok"] for c in valued), len(valued)),
        "recommendation_accuracy": ratio(sum(r["recommendation"]["ok"] for r in results), len(results)),
        "recommendation_confusion": confusion,
        "question_coverage": ratio(sum(len(r["questions"]["covered"]) for r in results), q_want),
        "status_accuracy_per_field": {n: status_acc(lambda c, n=n: c["field"] == n) for n in FIELDS},
        "status_accuracy_per_kind": {k: status_acc(lambda c, k=k: c["kind"] == k)
                                     for k in sorted({r["kind"] for r in results})},
    }


# ---------------------------------------------------------------- running

def load_cases(conn: Connection, case_ids: list[str] | None) -> list[dict]:
    rows = conn.execute(text("""SELECT case_id, kind, channel, studio, issued_at, expected, envelope_id
                                FROM eval.gold_case WHERE dataset_version = :v ORDER BY case_id"""),
                        dict(v=REQUEST_SET)).all()
    cases = []
    for r in rows:
        if case_ids and r.case_id not in case_ids:
            continue
        truth = {f: {"status": s, "value": v, "hint": h} for f, v, s, h in conn.execute(
            text("SELECT field, value, status, hint FROM eval.gold_truth WHERE dataset_version = :v AND case_id = :c"),
            dict(v=REQUEST_SET, c=r.case_id))}
        cases.append(dict(r._mapping) | {"fields": truth})
    return cases


def eval_copy(name: str, data: bytes) -> bytes:
    """The same content in different bytes. A workspace holds each file once, so the evaluation must not own the
    exact files a person might paste or drop in (the demo requests are in this set)."""
    suffix = Path(name).suffix.lower()
    if suffix in (".txt", ".eml"):
        return data + b"\n"
    if suffix == ".pdf":
        doc = pymupdf.open(stream=data, filetype="pdf")
        doc.set_metadata({**doc.metadata, "keywords": "bidwright evaluation copy"})
        return doc.tobytes(garbage=4, deflate=True, no_new_id=True)
    if suffix in (".png", ".jpg", ".jpeg", ".webp"):
        pix = pymupdf.Pixmap(data)
        pix.set_dpi(pix.xres + 1, pix.yres + 1)  # same pixels, a different density note
        return pix.tobytes("png")
    return data


def run_case(case: dict) -> int:
    """Ingest the request's files as they would arrive, and read them."""
    folder = SET_DIR / "requests" / case["case_id"]
    files = [(p.name, eval_copy(p.name, p.read_bytes())) for p in sorted(folder.iterdir()) if p.is_file()]
    with owner_engine.begin() as conn:  # removing the previous run's envelope switches off an append-only trigger
        if case["envelope_id"]:
            delete_envelope(conn, case["envelope_id"])
        envelope_id, created = ingest(conn, files, case["channel"], title=f"[eval {case['case_id']}]",
                                      received_at=case["issued_at"])
        if not created:
            raise RuntimeError(f"{case['case_id']}: its files already belong to envelope {envelope_id}")
        conn.execute(text("UPDATE eval.gold_case SET envelope_id = :e WHERE dataset_version = :v AND case_id = :c"),
                     dict(e=envelope_id, v=REQUEST_SET, c=case["case_id"]))
    extract_envelope(envelope_id)
    return envelope_id


def spend(conn: Connection, envelope_ids: list[int]) -> dict:
    row = conn.execute(text("""SELECT count(*), coalesce(sum(cost_usd), 0) FROM intake.llm_call WHERE run_id IN
                               (SELECT run_id FROM intake.extraction_run WHERE envelope_id = ANY(:e))"""),
                       dict(e=envelope_ids)).one()
    return {"llm_calls": row[0], "cost_usd": round(float(row[1]), 4)}


def pct(v: float | None) -> str:
    return "n/a" if v is None else f"{v:.0%}"


def report_md(meta: dict, s: dict, results: list[dict]) -> str:
    lines = [f"# Intake run {meta['run_at']}", "",
             f"Set `{REQUEST_SET}` · model `{meta['model']}` · prompt `{meta['prompt_version']}` · "
             f"engine `{meta['engine_version']}` · {s['requests']} requests · {meta['llm_calls']} model calls · "
             f"${meta['cost_usd']:.2f}", "",
             "| Measure | Result | Target |", "|---|---|---|",
             f"| Field status right | {pct(s['status_accuracy'])} | |",
             f"| Gaps caught (missing or ambiguous, not shown as found) | {s['gaps_caught']} ({pct(s['gap_recall'])}) | ≥ 90% |",
             f"| False gaps (stated, but shown as missing, ambiguous or estimated) | {s['false_gaps']} | low |",
             f"| Values right, where stated and shown | {pct(s['value_accuracy'])} | |",
             f"| Recommendation right (go, clarify, no-go) | {pct(s['recommendation_accuracy'])} | |",
             f"| Follow-up questions cover the gaps | {pct(s['question_coverage'])} | |", "",
             "## Recommendation (rows expected, columns got)", ""]
    outcomes = ["go", "clarify", "no-go", "none"]
    lines += ["| | " + " | ".join(outcomes) + " |", "|---|" + "---|" * len(outcomes)]
    for e in ("go", "clarify", "no-go"):
        row = s["recommendation_confusion"].get(e, {})
        lines.append(f"| {e} | " + " | ".join(str(row.get(o, 0)) for o in outcomes) + " |")
    lines += ["", "## Status right, per field", "", "| Field | Right |", "|---|---|"]
    lines += [f"| {n} | {pct(v)} |" for n, v in s["status_accuracy_per_field"].items()]
    lines += ["", "## Status right, per kind of request", "", "| Kind | Right |", "|---|---|"]
    lines += [f"| {k} | {pct(v)} |" for k, v in s["status_accuracy_per_kind"].items()]
    lines += ["", "## Per request", "", "| Request | Kind | Expected | Got | Rules missed | Rules extra | Status misses |",
              "|---|---|---|---|---|---|---|"]
    for r in results:
        misses = [f"{n} ({f['truth']}→{f['got']})" for n, f in r["fields"].items() if not f["status_ok"]]
        rec = r["recommendation"]
        lines.append(f"| {r['case_id']} | {r['kind']} | {rec['expected']} | {rec['got'] or 'none'} | "
                     f"{', '.join(r['rules']['missed']) or ''} | {', '.join(r['rules']['extra']) or ''} | "
                     f"{', '.join(misses)} |")
    return "\n".join(lines) + "\n"


def save(meta: dict, summary: dict, results: list[dict]) -> int:
    with engine.begin() as conn:
        run_id = conn.execute(text("""INSERT INTO eval.run (dataset_version, model, prompt_version, engine_version, summary)
                                      VALUES (:d, :m, :p, :e, CAST(:s AS jsonb)) RETURNING run_id"""),
                              dict(d=REQUEST_SET, m=meta["model"], p=meta["prompt_version"], e=meta["engine_version"],
                                   s=json.dumps(summary | {"cost_usd": meta["cost_usd"],
                                                           "llm_calls": meta["llm_calls"]}))).scalar_one()
        for r in results:
            conn.execute(text("INSERT INTO eval.case_result (run_id, case_id, envelope_id, detail) "
                              "VALUES (:r, :c, :e, CAST(:d AS jsonb))"),
                         dict(r=run_id, c=r["case_id"], e=r["envelope_id"], d=json.dumps(r, default=str)))
            conn.execute(text("INSERT INTO eval.field_result (run_id, case_id, field, truth, got, ok) "
                              "VALUES (:r, :c, :f, CAST(:t AS jsonb), CAST(:g AS jsonb), :ok)"),
                         [dict(r=run_id, c=r["case_id"], f=n,
                               t=json.dumps({"status": f["truth"], "value": f["truth_value"]}, default=str),
                               g=json.dumps({"status": f["got"], "value": f["got_value"]}, default=str),
                               ok=f["status_ok"]) for n, f in r["fields"].items()])
    return run_id


def evaluate(args) -> None:
    with engine.connect() as conn:
        cases = load_cases(conn, args.cases)
    if not cases:
        raise SystemExit(f"no {REQUEST_SET} cases in the database; run `make seed`")
    if args.rescore:
        missing = [c["case_id"] for c in cases if not c["envelope_id"]]
        if missing:
            raise SystemExit(f"never run: {', '.join(missing)}; run without --rescore first")
        envelope_ids = [c["envelope_id"] for c in cases]
    else:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            envelope_ids = list(pool.map(lambda c, ctx: ctx.run(run_case, c), cases,
                                         [contextvars.copy_context() for _ in cases]))
    with engine.connect() as conn:
        results = [score(c, e, predict(conn, e)) for c, e in zip(cases, envelope_ids)]
        meta = {"run_at": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"), "model": settings.model_extract,
                "prompt_version": PROMPT_VERSION, "engine_version": ENGINE_VERSION} | spend(conn, envelope_ids)
    summary = summarise(results)
    save(meta, summary, results)
    md = report_md(meta, summary, results)
    out = SET_DIR / "runs"
    out.mkdir(exist_ok=True)
    (out / f"{meta['run_at']}.md").write_text(md)
    print(md)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", nargs="*")
    parser.add_argument("--rescore", action="store_true", help="score the last run's envelopes, no model calls")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--workspace", default="northbeam")
    args = parser.parse_args()
    with engine.connect() as conn:
        org_id = conn.execute(text("SELECT org_id FROM tenancy.organization WHERE slug = :s"),
                              dict(s=args.workspace)).scalar()
    if org_id is None:
        raise SystemExit(f"no workspace '{args.workspace}'")
    with workspace(org_id):
        evaluate(args)


if __name__ == "__main__":
    main()
