"""Run the pipeline over the gold packs and score it against the truth.

    uv run python -m sog.evaluation                 # all packs
    uv run python -m sog.evaluation --cases P02 P13 # some packs
    uv run python -m sog.evaluation --rescore       # re-score the last run's envelopes, no model calls

Writes data/gold/<dataset>/runs/<timestamp>.json and .md
"""

import argparse
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from rapidfuzz import fuzz
from sqlalchemy import text

from sog.config import REPO_ROOT, settings
from sog.db import engine
from sog.engine import ENGINE_VERSION, LEGAL_SUFFIXES
from sog.extraction import PROMPT_VERSION, extract_envelope
from sog.intake.ingest import delete_envelope, ingest
from sog.record import build
from sog.storage import blob_store

GOLD = REPO_ROOT / "data" / "gold" / settings.sog_dataset_version
PRICING_CRITICAL = ["typology", "gfa_m2", "site_area_m2", "fitout_area_m2", "landscape_area_m2", "services",
                    "stage_package", "country", "city"]
OTHER = ["client_name", "client_status", "units", "keys", "submission_deadline", "liability_cap", "payment_terms_days"]


def clean(name: str) -> str:
    return re.sub(r"\s+", " ", re.sub(LEGAL_SUFFIXES, "", (name or "").lower())).strip(" .,")


def correct(field: str, truth, got) -> bool:
    if truth in (None, 0) and field.endswith("_m2"):
        return got in (None, 0)
    if truth is None:
        return got in (None, [], "")
    if got in (None, [], ""):
        return False
    if field.endswith("_m2"):
        return abs(float(got) - float(truth)) <= 0.015 * float(truth)
    if field in ("services", "stage_package"):
        return set(got) == set(truth)
    if field == "client_name":
        return fuzz.token_sort_ratio(clean(got), clean(truth)) >= 90
    if field == "city":
        return str(got).lower() == str(truth).lower()
    if field == "submission_deadline":
        return datetime.fromisoformat(got) == datetime.fromisoformat(truth)
    return got == truth


def load_cases(conn, case_ids: list[str] | None = None) -> list[dict]:
    """Gold cases with their expected values, from the eval schema."""
    rows = conn.execute(text("""SELECT case_id, proposal_id, style, issued_at, traps, special_requests, envelope_id
                                FROM eval.gold_case WHERE dataset_version = :v ORDER BY case_id"""),
                        dict(v=settings.sog_dataset_version)).all()
    cases = []
    for r in rows:
        if case_ids and r.case_id not in case_ids:
            continue
        key = dict(v=settings.sog_dataset_version, c=r.case_id)
        fields = dict(conn.execute(text("SELECT field, value FROM eval.gold_truth WHERE dataset_version = :v "
                                        "AND case_id = :c"), key).all())
        conflicts = [dict(field=f, values=vals, documents=docs) for f, vals, docs in conn.execute(
            text("SELECT field, values, documents FROM eval.gold_conflict WHERE dataset_version = :v AND case_id = :c"),
            key)]
        cases.append(dict(r._mapping) | dict(fields=fields, conflicts=conflicts))
    return cases


def run_case(case: dict) -> int:
    """Re-ingest the pack's original files into a fresh envelope and extract it."""
    with engine.begin() as conn:
        files = [(name, blob_store.get(uri)) for name, uri in conn.execute(
            text("SELECT file_name, blob_uri FROM lineage.source_document WHERE envelope_id = :e "
                 "AND parent_document_id IS NULL ORDER BY document_id"), dict(e=case["envelope_id"]))]
        if not files:
            raise RuntimeError(f"gold case {case['case_id']} has no envelope; run the loader")
        delete_envelope(conn, case["envelope_id"])
        envelope_id, _ = ingest(conn, files, "upload", title=f"[gold {case['case_id']}]", received_at=case["issued_at"])
        conn.execute(text("UPDATE eval.gold_case SET envelope_id = :e WHERE dataset_version = :v AND case_id = :c"),
                     dict(e=envelope_id, v=settings.sog_dataset_version, c=case["case_id"]))
    extract_envelope(envelope_id)
    return envelope_id


def score(case: dict, envelope_id: int) -> dict:
    with engine.connect() as conn:
        rec = build(conn, envelope_id)
        env = conn.execute(text("SELECT lead_status, completeness FROM intake.envelope WHERE envelope_id = :e"),
                           dict(e=envelope_id)).one()
        conflicts = conn.execute(text("SELECT field, summary FROM intake.field_conflict WHERE envelope_id = :e"),
                                 dict(e=envelope_id)).all()
        spend = conn.execute(text("""SELECT count(*), sum(cost_usd), max(latency_ms), sum(input_tokens), sum(output_tokens),
                                     count(*) FILTER (WHERE validation_error IS NOT NULL)
                                     FROM intake.llm_call WHERE run_id IN
                                     (SELECT run_id FROM intake.extraction_run WHERE envelope_id = :e)"""),
                             dict(e=envelope_id)).one()
        ai_sources = [s for f in rec.values() if isinstance(f, dict) for s in f.get("sources", [])
                      if s["origin"] == "ai" and s["status"] == "proposed"]
    fields = {}
    for name in PRICING_CRITICAL + OTHER:
        truth, got = case["fields"].get(name), rec[name]["value"]
        fields[name] = {"truth": truth, "got": got, "ok": correct(name, truth, got)}
    planted = {c["field"] for c in case["conflicts"]}
    found = {c.field for c in conflicts}
    return {
        "proposal_id": case["proposal_id"], "envelope_id": envelope_id, "style": case["style"], "traps": case["traps"],
        "fields": fields,
        "conflicts": {"planted": sorted(planted), "found": sorted(found),
                      "detected": sorted(planted & found), "false": sorted(found - planted),
                      "summaries": [c.summary for c in conflicts]},
        "citations": {"values": len(ai_sources),
                      "with_box": sum(1 for s in ai_sources if s["bbox"] and s["page"]),
                      "verbatim": sum(1 for s in ai_sources if s["quote_verbatim"])},
        "lead_status": env.lead_status, "completeness": float(env.completeness or 0),
        "llm": {"calls": spend[0], "cost_usd": float(spend[1] or 0), "max_latency_ms": spend[2],
                "input_tokens": spend[3], "output_tokens": spend[4], "rejected_attempts": spend[5]},
    }


def summarise(results: list[dict]) -> dict:
    def acc(names):
        cells = [r["fields"][n]["ok"] for r in results for n in names]
        return round(sum(cells) / len(cells), 4) if cells else None
    per_field = {n: acc([n]) for n in PRICING_CRITICAL + OTHER}
    planted = sum(len(r["conflicts"]["planted"]) for r in results)
    detected = sum(len(r["conflicts"]["detected"]) for r in results)
    cites = [r["citations"] for r in results]
    return {
        "pricing_critical_accuracy": acc(PRICING_CRITICAL),
        "all_fields_accuracy": acc(PRICING_CRITICAL + OTHER),
        "per_field": per_field,
        "conflict_recall": f"{detected}/{planted}",
        "false_conflicts": sum(len(r["conflicts"]["false"]) for r in results),
        "citation_coverage": round(sum(c["with_box"] for c in cites) / max(1, sum(c["values"] for c in cites)), 4),
        "quote_verbatim_rate": round(sum(c["verbatim"] for c in cites) / max(1, sum(c["values"] for c in cites)), 4),
        "cost_usd": round(sum(r["llm"]["cost_usd"] for r in results), 4),
        "llm_calls": sum(r["llm"]["calls"] for r in results),
        "rejected_attempts": sum(r["llm"]["rejected_attempts"] or 0 for r in results),
    }


def report_md(meta: dict, summary: dict, results: list[dict]) -> str:
    lines = [f"# Gold-set run {meta['run_at']}", "",
             f"Dataset `{meta['dataset']}` · model `{meta['model']}` · prompt `{meta['prompt_version']}` · "
             f"engine `{meta['engine_version']}` · {len(results)} packs", "",
             "| Measure | Result | Target |", "|---|---|---|",
             f"| Pricing-critical field accuracy | {summary['pricing_critical_accuracy']:.1%} | ≥ 90% |",
             f"| All scored fields | {summary['all_fields_accuracy']:.1%} | |",
             f"| Values with a highlighted source | {summary['citation_coverage']:.1%} | 100% |",
             f"| Quotes found verbatim in the cited lines | {summary['quote_verbatim_rate']:.1%} | |",
             f"| Planted conflicts flagged | {summary['conflict_recall']} | all |",
             f"| Conflicts flagged that were not planted | {summary['false_conflicts']} | |",
             f"| Model calls / rejected attempts | {summary['llm_calls']} / {summary['rejected_attempts']} | |",
             f"| Model cost | ${summary['cost_usd']:.2f} | |", "",
             "## Accuracy per field", "", "| Field | Accuracy |", "|---|---|"]
    lines += [f"| {n}{' *' if n in PRICING_CRITICAL else ''} | {v:.0%} |" for n, v in summary["per_field"].items()]
    lines += ["", "\\* pricing-critical", "", "## Misses", "", "| Pack | Field | Truth | Got |", "|---|---|---|---|"]
    for r in results:
        for n, f in r["fields"].items():
            if not f["ok"]:
                lines.append(f"| {r['proposal_id']} | {n} | {f['truth']} | {f['got']} |")
    lines += ["", "## Conflicts flagged", ""]
    lines += [f"- {r['proposal_id']}: {s}" for r in results for s in r["conflicts"]["summaries"]] or ["- none"]
    return "\n".join(lines) + "\n"


def save(meta: dict, summary: dict, results: list[dict]) -> int:
    with engine.begin() as conn:
        run_id = conn.execute(text("""INSERT INTO eval.run (dataset_version, model, prompt_version, engine_version, summary)
                                      VALUES (:d, :m, :p, :e, CAST(:s AS jsonb)) RETURNING run_id"""),
                              dict(d=meta["dataset"], m=meta["model"], p=meta["prompt_version"],
                                   e=meta["engine_version"], s=json.dumps(summary))).scalar_one()
        for r in results:
            conn.execute(text("INSERT INTO eval.case_result VALUES (:r, :c, :e, CAST(:d AS jsonb))"),
                         dict(r=run_id, c=r["proposal_id"], e=r["envelope_id"], d=json.dumps(r, default=str)))
            conn.execute(text("INSERT INTO eval.field_result VALUES (:r, :c, :f, CAST(:t AS jsonb), CAST(:g AS jsonb), :ok)"),
                         [dict(r=run_id, c=r["proposal_id"], f=f, t=json.dumps(v["truth"], default=str),
                               g=json.dumps(v["got"], default=str), ok=v["ok"]) for f, v in r["fields"].items()])
    return run_id


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", nargs="*")
    parser.add_argument("--rescore", action="store_true", help="score the existing gold envelopes without re-running")
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args()
    with engine.connect() as conn:
        cases = load_cases(conn, args.cases)
    if not cases:
        raise SystemExit("no gold cases in the database; run `uv run python -m sog.archive.loader`")
    if args.rescore:
        envelope_ids = [c["envelope_id"] for c in cases]
    else:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            envelope_ids = list(pool.map(run_case, cases))
    results = [score(c, e) for c, e in zip(cases, envelope_ids)]
    summary = summarise(results)
    run_at = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    meta = {"run_at": run_at, "dataset": settings.sog_dataset_version, "model": settings.sog_model_extract,
            "prompt_version": PROMPT_VERSION, "engine_version": ENGINE_VERSION}
    run_id = save(meta, summary, results)
    md = report_md(meta | {"run_id": run_id}, summary, results)
    out = GOLD / "runs"
    out.mkdir(exist_ok=True)
    (out / f"{run_at}.md").write_text(md)
    print(md)


if __name__ == "__main__":
    main()
