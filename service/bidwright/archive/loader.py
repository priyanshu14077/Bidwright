"""Load reference data, and the demo workspace with its sample archive.

Three layers:
- shared: countries, FX, the typology/service/stage vocabulary and the request
  schema. One copy for every workspace.
- starter pack: markets and tiers, synonyms, field weights and bid rules. Every
  new workspace gets a copy (seed_workspace) and edits it from there.
- sample archive and gold set: the fictional Northbeam Studio's 14 proposals
  and RFP packs, loaded into the demo workspace only.

Every load converges: running it twice leaves the same state.

    uv run python -m bidwright.archive.loader            # shared data + demo workspace
    uv run python -m bidwright.archive.loader --skip-gold
"""

import argparse
import json
import re
from datetime import datetime
from pathlib import Path

from docx import Document
from sqlalchemy import Connection, text

from bidwright.config import REPO_ROOT, settings
from bidwright import tenancy
from bidwright.db import owner_engine, workspace
from bidwright.intake import reading
from bidwright.intake.ingest import delete_envelope, ingest
from bidwright.storage import blob_store

REFERENCE_FILE = REPO_ROOT / "data" / "reference" / "v0.json"

# The synthetic JSON's list of services uses "interior"; ref.service uses "interior_design".
SERVICE_ALIASES = {"interior": "interior_design"}

SECTION_CODES = {
    "project understanding": "understanding",
    "scope of services": "scope",
    "deliverables": "deliverables",
    "programme": "programme",
    "fee proposal": "fees",
    "reimbursable expenses": "reimbursables",
    "payment schedule": "payment",
    "exclusions": "exclusions",
    "terms and conditions": "terms",
}


DEMO_ORG = {"slug": "northbeam", "name": "Northbeam Studio",
            "practice_description": "an international architecture, interior design, landscape and masterplanning "
                                    "practice with studios in Dubai, Barcelona, Singapore, Ho Chi Minh City and "
                                    "Guangzhou"}
DEMO_USER = {"email": "demo@northbeam.example", "name": "Demo Estimator", "password": "northbeam-demo"}


def load_shared(conn: Connection, path: Path = REFERENCE_FILE) -> None:
    """Countries, FX and the shared vocabulary. Run with the owner login, outside any workspace."""
    ref = json.loads(path.read_text())
    upserts = [
        (
            "INSERT INTO ref.country VALUES (:code, :name, :region, :cur, :unit, :aliases) "
            "ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name, region = EXCLUDED.region, "
            "national_currency = EXCLUDED.national_currency, area_unit = EXCLUDED.area_unit, "
            "aliases = EXCLUDED.aliases",
            [
                dict(code=c, name=n, region=r, cur=cur, unit=u, aliases=a)
                for c, n, r, cur, u, a in ref["countries"]
            ],
        ),
        (
            "INSERT INTO ref.fx_rate VALUES (:cur, :rate, :as_of, :note) "
            "ON CONFLICT (currency, as_of) DO UPDATE SET per_usd = EXCLUDED.per_usd, note = EXCLUDED.note",
            [dict(cur=c, rate=r, as_of=ref["fx_as_of"], note=n) for c, r, n in ref["fx_rates"]],
        ),
        (
            "INSERT INTO ref.typology VALUES (:code, :label) "
            "ON CONFLICT (code) DO UPDATE SET label = EXCLUDED.label",
            [dict(code=c, label=lbl) for c, lbl in ref["typologies"]],
        ),
        (
            "INSERT INTO ref.service VALUES (:code, :label, :basis) "
            "ON CONFLICT (code) DO UPDATE SET label = EXCLUDED.label, default_area_basis = EXCLUDED.default_area_basis",
            [dict(code=c, label=lbl, basis=b) for c, lbl, b in ref["services"]],
        ),
        (
            "INSERT INTO ref.stage VALUES (:code, :label, :seq) "
            "ON CONFLICT (code) DO UPDATE SET label = EXCLUDED.label, seq = EXCLUDED.seq",
            [dict(code=c, label=lbl, seq=s) for c, lbl, s in ref["stages"]],
        ),
    ]
    for sql, rows in upserts:
        conn.execute(text(sql), rows)


def seed_workspace(conn: Connection, path: Path = REFERENCE_FILE) -> None:
    """Copy the starter pack into the current workspace. Upserts, so it also refreshes the demo workspace."""
    ref = json.loads(path.read_text())
    upserts = [
        (
            "INSERT INTO ref.location (country, city, tier, aliases) VALUES (:c, :city, :tier, :aliases) "
            "ON CONFLICT (org_id, country, city) DO UPDATE SET tier = EXCLUDED.tier, aliases = EXCLUDED.aliases",
            [dict(c=c, city=city, tier=t, aliases=a) for c, city, t, a in ref["locations"]],
        ),
        (
            "INSERT INTO ref.term_synonym (synonym, target_table, target_code, scope, language) "
            "VALUES (:syn, :tbl, :code, :scope, :lang) "
            "ON CONFLICT (org_id, synonym, target_table, scope) DO UPDATE SET target_code = EXCLUDED.target_code, "
            "language = EXCLUDED.language",
            [dict(syn=s, tbl=t, code=c, scope=sc, lang=lg) for s, t, c, sc, lg in ref["synonyms"]],
        ),
        (
            "INSERT INTO ref.field_criticality (field, pricing_critical, weight) VALUES (:field, :crit, :w) "
            "ON CONFLICT (org_id, field) DO UPDATE SET pricing_critical = EXCLUDED.pricing_critical, "
            "weight = EXCLUDED.weight",
            [dict(field=f, crit=c, w=w) for f, c, w in ref["field_criticality"]],
        ),
        (
            "INSERT INTO ref.qualification_rule (name, expression, effect, message, owner) "
            "VALUES (:name, CAST(:expr AS jsonb), :effect, :message, 'starter-pack') "
            "ON CONFLICT (org_id, name) DO UPDATE SET expression = EXCLUDED.expression, effect = EXCLUDED.effect, "
            "message = EXCLUDED.message, updated_at = now()",
            [
                dict(name=r["name"], expr=json.dumps(r["expression"]), effect=r["effect"], message=r["message"])
                for r in ref["qualification_rules"]
            ],
        ),
    ]
    for sql, rows in upserts:
        conn.execute(text(sql), rows)


def country_codes(conn: Connection) -> dict[str, str]:
    """Lower-cased name, alias or code -> ISO code."""
    out: dict[str, str] = {}
    for code, name, aliases in conn.execute(text("SELECT code, name, aliases FROM ref.country")):
        for key in [code, name, *aliases]:
            out[key.lower()] = code
    return out


def load_archive(conn: Connection, dataset_version: str) -> int:
    dataset_dir = REPO_ROOT / "data" / "archive" / dataset_version
    data = json.loads((dataset_dir / "archive.json").read_text())
    countries = country_codes(conn)
    data_origin = "synthetic" if "synthetic" in dataset_version else "archive"

    conn.execute(text("DELETE FROM archive.proposal WHERE dataset_version = :v"), dict(v=dataset_version))
    conn.execute(text("DELETE FROM archive.client WHERE dataset_version = :v"), dict(v=dataset_version))
    meta = data["meta"]
    conn.execute(
        text("""INSERT INTO archive.dataset (dataset_version, name, data_origin, generated, record_count, warning)
                VALUES (:v, :name, :origin, :generated, :n, :warning)
                ON CONFLICT (org_id, dataset_version) DO UPDATE SET name = EXCLUDED.name, data_origin = EXCLUDED.data_origin,
                generated = EXCLUDED.generated, record_count = EXCLUDED.record_count, warning = EXCLUDED.warning,
                loaded_at = now()"""),
        dict(v=dataset_version, name=meta["name"], origin=data_origin, generated=meta.get("generated"),
             n=meta.get("record_count"), warning=meta.get("warning")),
    )
    conn.execute(text("DELETE FROM ref.fee_assumption WHERE dataset_version = :v"), dict(v=dataset_version))
    conn.execute(text("INSERT INTO ref.fee_assumption (key, value_text, value, unit, rationale, dataset_version) "
                      "VALUES (:key, :text, :value, :unit, :rationale, :v)"),
                 [dict(a, text=str(a["value"]), value=a["value"] if isinstance(a["value"], (int, float)) else None,
                       v=dataset_version) for a in data["assumptions"]])
    conn.execute(text("DELETE FROM ref.typology_cost_ratio WHERE dataset_version = :v"), dict(v=dataset_version))
    conn.execute(text("INSERT INTO ref.typology_cost_ratio (cost_type, ratio, dataset_version) VALUES (:t, :r, :v)"),
                 [dict(t=t, r=r, v=dataset_version) for t, r in data["typology_cost_ratios"].items()])

    conn.execute(
        text(
            "INSERT INTO ref.studio (code, name, city, country) VALUES (:code, :name, :city, :country) "
            "ON CONFLICT (org_id, code) DO UPDATE SET name = EXCLUDED.name, city = EXCLUDED.city, country = EXCLUDED.country"
        ),
        [dict(s, country=countries[s["country"].lower()]) for s in data["studios"]],
    )
    conn.execute(
        text("INSERT INTO archive.client (client_id, dataset_version, name, client_type, country) "
             "VALUES (:client_id, :v, :name, :type, :country)"),
        [dict(c, v=dataset_version, country=countries[c["country"].lower()]) for c in data["clients"]],
    )
    conn.execute(text("DELETE FROM ref.benchmark_source WHERE source_key = ANY(:keys)"),
                 dict(keys=list({b["source_key"] for b in data["benchmarks"]})))
    conn.execute(
        text(
            "INSERT INTO ref.benchmark_source (source_key, market, metric, value, unit, year, source_name, url, note) "
            "VALUES (:source_key, :market, :metric, :value, :unit, :year, :source, :url, :note)"
        ),
        [dict(b, value=str(b["value"])) for b in data["benchmarks"]],
    )

    for p in data["proposals"]:
        country = countries[p["country"].lower()]
        location_id = conn.execute(
            text("SELECT location_id FROM ref.location WHERE country = :c AND city = :city"),
            dict(c=country, city=p["city"]),
        ).scalar_one()
        conn.execute(
            text(
                """
                INSERT INTO archive.proposal (
                  proposal_id, dataset_version, reference, title, data_origin, studio_code, client_id,
                  client_status, location_id, typology, sub_typology, gfa_m2, site_area_m2,
                  landscape_area_m2, fitout_area_m2, units, keys, complexity, complexity_notes,
                  submission_date, currency, fx_per_usd, construction_cost_usd_m2,
                  fee_total_local, fee_total_usd, status, loss_reason, project_challenges, cost_city,
                  document_variations)
                VALUES (
                  :proposal_id, :v, :reference, :title, :origin, :studio_code, :client_id,
                  :client_status, :location_id, :typology, :cost_type, :gfa_m2, :site_area_m2,
                  :landscape_area_m2, :fitout_area_m2, :units, :keys, :complexity, :complexity_notes,
                  :submission_date, :currency, :fx_per_usd, :construction_cost_usd_m2,
                  :fee_total_local, :fee_total_usd, :status, :loss_reason, :project_challenges, :cost_city,
                  CAST(:variations AS jsonb))
                """
            ),
            dict(p, v=dataset_version, origin=data_origin, location_id=location_id,
                 variations=json.dumps(p.get("document_variations"))),
        )
        pid = p["proposal_id"]
        services = sorted({SERVICE_ALIASES.get(s, s) for s in p["services"]})
        conn.execute(text("INSERT INTO archive.proposal_service (proposal_id, service) VALUES (:p, :s)"),
                     [dict(p=pid, s=s) for s in services])
        conn.execute(text("INSERT INTO archive.proposal_stage (proposal_id, stage) VALUES (:p, :s)"),
                     [dict(p=pid, s=s) for s in p["stage_package"]])
        conn.execute(
            text(
                "INSERT INTO archive.fee_line (proposal_id, line_no, service, stage, basis, basis_qty, basis_unit, "
                "amount_local, amount_usd) VALUES (:p, :line_no, :service, :stage, :basis, "
                ":basis_qty, :basis_unit, :amount_local, :amount_usd)"
            ),
            [dict(f, p=pid, service=SERVICE_ALIASES.get(f["service"], f["service"])) for f in p["fee_lines"]],
        )
        if p["reimbursables"]:
            conn.execute(
                text("INSERT INTO archive.reimbursable (proposal_id, type, qty, unit_rate_local, amount_local, note) "
                     "VALUES (:p, :type, :qty, :unit_rate_local, :amount_local, :note)"),
                [dict(r, p=pid) for r in p["reimbursables"]],
            )
        conn.execute(
            text("INSERT INTO archive.payment_milestone (proposal_id, seq, milestone, pct, amount_local) "
                 "VALUES (:p, :seq, :milestone, :pct, :amount_local)"),
            [dict(m, p=pid) for m in p["payment_schedule"]],
        )
        conn.execute(text("INSERT INTO archive.deliverable (proposal_id, item, qty) VALUES (:p, :item, :qty)"),
                     [dict(d, p=pid) for d in p["deliverables"]])
        if p["tc_deviations"]:
            conn.execute(
                text("INSERT INTO archive.tc_deviation (proposal_id, clause, client_request, firm_position) "
                     "VALUES (:p, :clause, :client_request, :firm_position)"),
                [dict(t, p=pid) for t in p["tc_deviations"]],
            )
        if p.get("pricing_trace"):
            conn.execute(
                text("INSERT INTO archive.pricing_trace (proposal_id, factor, value, source, note) "
                     "VALUES (:p, :factor, :value, :source, :note)"),
                [dict(t, p=pid, value=str(t["value"])) for t in p["pricing_trace"]],
            )
        docx_path = next((dataset_dir / "proposals").glob(f"{pid}_*.docx"), None)
        if docx_path:
            load_proposal_document(conn, pid, docx_path)
    return len(data["proposals"])


def load_proposal_document(conn: Connection, proposal_id: str, path: Path) -> None:
    data = path.read_bytes()
    sha, uri = blob_store.put(data, path.name)
    conn.execute(text("DELETE FROM lineage.source_document WHERE sha256 = :sha"), dict(sha=sha))
    pdf = reading.render_pdf(path.name, data)
    render_uri = blob_store.put(pdf, path.stem + ".render.pdf")[1]
    pages = reading.read_layout(pdf)
    document_id = conn.execute(
        text(
            "INSERT INTO lineage.source_document (proposal_id, channel, file_name, mime_type, size_bytes, "
            "sha256, blob_uri, render_uri, doc_class, page_count) "
            "VALUES (:p, 'archive', :name, :mime, :size, :sha, :uri, :render, 'proposal', :pages) RETURNING document_id"
        ),
        dict(p=proposal_id, name=path.name, size=len(data), sha=sha, uri=uri, render=render_uri, pages=len(pages),
             mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    ).scalar_one()
    conn.execute(
        text("INSERT INTO lineage.page (document_id, page_no, width, height, lines) "
             "VALUES (:d, :n, :w, :h, CAST(:lines AS jsonb))"),
        [dict(d=document_id, n=pg.page_no, w=pg.width, h=pg.height,
              lines=json.dumps([{"id": ln.line_id, "text": ln.text, "bbox": ln.bbox} for ln in pg.lines]))
         for pg in pages],
    )
    conn.execute(
        text(
            "INSERT INTO archive.proposal_section (proposal_id, seq, section_code, heading, body) "
            "VALUES (:p, :seq, :code, :heading, :body)"
        ),
        [dict(s, p=proposal_id) for s in docx_sections(path)],
    )


def docx_sections(path: Path) -> list[dict]:
    """Split a proposal on its numbered headings ("1. Project Understanding")."""
    doc = Document(str(path))
    body = doc.element.body
    sections: list[dict] = []
    for block in body.iterchildren():
        tag = block.tag.rsplit("}", 1)[-1]
        if tag == "p":
            line = "".join(t.text or "" for t in block.iter() if t.tag.endswith("}t")).strip()
            m = re.match(r"^(\d+)\.\s+(.+)$", line)
            if m and m.group(2).lower() in SECTION_CODES:
                heading = m.group(2)
                sections.append(dict(seq=len(sections) + 1, code=SECTION_CODES[heading.lower()],
                                     heading=heading, lines=[]))
                continue
        elif tag == "tbl":
            rows = []
            for tr in block.iter():
                if tr.tag.endswith("}tr"):
                    cells = ["".join(t.text or "" for t in tc.iter() if t.tag.endswith("}t"))
                             for tc in tr if tc.tag.endswith("}tc")]
                    rows.append(" | ".join(cells))
            line = "\n".join(rows)
        else:
            continue
        if sections and line:
            sections[-1]["lines"].append(line)
    return [dict(seq=s["seq"], code=s["code"], heading=s["heading"], body="\n".join(s["lines"])) for s in sections]


def load_schema(conn: Connection) -> list[str]:
    versions = []
    for path in sorted((REPO_ROOT / "data" / "schema").glob("*.json")):
        definition = json.loads(path.read_text())
        conn.execute(text("INSERT INTO ref.schema_definition VALUES (:v, CAST(:d AS jsonb), now()) "
                          "ON CONFLICT (version) DO UPDATE SET definition = EXCLUDED.definition, loaded_at = now()"),
                     dict(v=definition["version"], d=json.dumps(definition)))
        versions.append(definition["version"])
    return versions


def load_gold(conn: Connection, dataset_version: str) -> int:
    """Each gold RFP pack becomes a received envelope in the inbox, linked to its expected values."""
    gold_dir = REPO_ROOT / "data" / "gold" / dataset_version
    truth_file = gold_dir / "truth.json"
    if not truth_file.exists():
        return 0
    cases = json.loads(truth_file.read_text())["cases"]
    for envelope_id in conn.execute(text("SELECT envelope_id FROM intake.envelope WHERE title LIKE '[gold %' "
                                         "AND dataset_version = :v"), dict(v=dataset_version)).scalars().all():
        delete_envelope(conn, envelope_id)
    conn.execute(text("DELETE FROM eval.gold_case WHERE dataset_version = :v"), dict(v=dataset_version))
    for case_id, case in sorted(cases.items()):
        pack = gold_dir / case["pack"]
        files = [(f.name, f.read_bytes()) for f in sorted(pack.iterdir()) if f.is_file()]
        envelope_id, _ = ingest(conn, files, "upload", title=f"[gold {case_id}]",
                                received_at=datetime.fromisoformat(case["issued_at"]))
        key = dict(v=dataset_version, c=case_id)
        conn.execute(text("""INSERT INTO eval.gold_case (dataset_version, case_id, proposal_id, style, issued_at,
                                traps, special_requests, envelope_id)
                             VALUES (:v, :c, :p, :style, :issued, :traps, :special, :e)"""),
                     key | dict(p=case["proposal_id"], style=case["style"], issued=case["issued_at"],
                                traps=case["traps"], special=case["special_requests"], e=envelope_id))
        conn.execute(text("INSERT INTO eval.gold_truth (dataset_version, case_id, field, value) "
                          "VALUES (:v, :c, :f, CAST(:val AS jsonb))"),
                     [key | dict(f=f, val=json.dumps(val)) for f, val in case["fields"].items()])
        if case["conflicts"]:
            conn.execute(text("INSERT INTO eval.gold_conflict (dataset_version, case_id, field, values, documents) "
                              "VALUES (:v, :c, :f, CAST(:vals AS jsonb), :docs)"),
                         [key | dict(f=c["field"], vals=json.dumps(c["values"]), docs=c["documents"])
                          for c in case["conflicts"]])
    return len(cases)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default=settings.dataset_version)
    parser.add_argument("--skip-gold", action="store_true", help="do not (re)load the gold RFP packs into the inbox")
    args = parser.parse_args()
    with owner_engine.begin() as conn:
        load_shared(conn)
        schemas = load_schema(conn)
        org_id = tenancy.ensure_demo(conn, DEMO_ORG, DEMO_USER)
    with workspace(org_id), owner_engine.begin() as conn:
        seed_workspace(conn)
        n = load_archive(conn, args.dataset)
        g = 0 if args.skip_gold else load_gold(conn, args.dataset)
    print(f"loaded shared data and schemas {schemas}; demo workspace '{DEMO_ORG['name']}' has {n} proposals "
          f"and {g} gold RFP packs from {args.dataset}")
    print(f"demo sign-in: {DEMO_USER['email']} / {DEMO_USER['password']}")


if __name__ == "__main__":
    main()
