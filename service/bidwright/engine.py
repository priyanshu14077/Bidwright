"""Reference engine (pass 2). Deterministic: no model calls.

For one envelope it
1. normalises every AI mention against the workspace's reference data (units, synonyms, places),
2. consolidates mentions across documents and flags conflicts instead of resolving them,
3. enriches: location tier, billing currency, routed studio, client match,
4. checks plausibility against the archive and suggests values for gaps,
5. scores completeness and applies the qualification rules.

The AI suggests; the engine only accepts values in the controlled lists.
"""

import json
import math
import re
import statistics
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from rapidfuzz import fuzz
from sqlalchemy import Connection, text

from bidwright.config import settings
from bidwright.db import engine
from bidwright.schema import SCHEMA_VERSION, Field, fields

ENGINE_VERSION = "engine-v0"
TO_M2 = {"m2": 1.0, "sqft": 0.09290304, "ha": 10000.0, "acre": 4046.8564224}
SERVICE_AREA = {"architecture": "gfa_m2", "interior_design": "fitout_area_m2", "landscape": "landscape_area_m2",
                "masterplan": "site_area_m2"}
LEGAL_SUFFIXES = r"\b(llc|l\.l\.c\.|pvt|ltd|limited|pte|jsc|joint stock company|s\.a\.|s\.l\.|co\.|company|inc|group|holdings)\b"

# Which document class is the better source for a field, highest first.
SOURCE_PRIORITY = {
    "area": ["area_schedule", "main_rfp", "design_brief", "email", "other", "client_terms"],
    "submission_deadline": ["email", "main_rfp", "design_brief", "other"],
    "issue_date": ["email", "main_rfp", "design_brief", "other"],
    "liability_cap": ["client_terms", "main_rfp", "other"],
    "payment_terms_days": ["client_terms", "main_rfp", "other"],
    "default": ["main_rfp", "design_brief", "email", "area_schedule", "client_terms", "other"],
}


@dataclass
class Candidate:
    field_value_id: int
    field: str
    value: dict
    document_id: int | None
    doc_class: str | None
    confidence: float
    normalized: object = None
    status: str = "proposed"
    notes: list[str] = field(default_factory=list)


@dataclass
class Flag:
    rule: str
    severity: str
    message: str
    field: str | None = None
    field_value_id: int | None = None
    evidence: dict | None = None


class Reference:
    def __init__(self, conn: Connection):
        self.synonyms: dict[tuple[str, str, str], str] = {
            (t, s, syn): c for syn, t, c, s in conn.execute(
                text("SELECT synonym, target_table, target_code, scope FROM ref.term_synonym"))}
        self.codes = {t: set(conn.execute(text(f"SELECT code FROM ref.{t}")).scalars())
                      for t in ("typology", "service", "stage")}
        self.countries: dict[str, str] = {}
        self.country_rows = {}
        for code, name, region, cur, aliases in conn.execute(
                text("SELECT code, name, region, national_currency, aliases FROM ref.country")):
            self.country_rows[code] = dict(name=name, region=region, currency=cur)
            for key in [code, name, *aliases]:
                self.countries[key.lower()] = code
        self.locations = [dict(r._mapping) for r in conn.execute(
            text("SELECT location_id, country, city, tier, aliases FROM ref.location"))]
        self.studios = [dict(r._mapping) for r in conn.execute(text("SELECT code, name, city, country FROM ref.studio"))]
        self.criticality = {f: float(w) for f, crit, w in conn.execute(
            text("SELECT field, pricing_critical, weight FROM ref.field_criticality")) if crit}
        self.rules = [dict(r._mapping) for r in conn.execute(
            text("SELECT name, expression, effect, message FROM ref.qualification_rule WHERE active"))]

    def vocab(self, table: str, raw: str, country: str | None) -> set[str]:
        """Controlled codes a written term maps to, trying the whole term, its parenthetical and its parts."""
        s = raw.lower().strip()
        pieces = [s, re.sub(r"\(.*?\)", "", s).strip(), *re.findall(r"\((.*?)\)", s)]
        for sep in (" / ", "/", " - ", " – ", ":"):
            pieces += [p.strip() for p in s.split(sep)]
        for piece in pieces:
            piece = re.sub(r"[.;,]+$", "", piece).strip()
            for scope in (country, "global"):
                if scope and (table, scope, piece) in self.synonyms:
                    return {self.synonyms[(table, scope, piece)]}
        found = set()
        for piece in pieces:
            for scope in (country, "global"):
                code = self.synonyms.get((table, scope or "", piece))
                if code:
                    found.add(code)
        return found

    def country(self, raw: str | None) -> str | None:
        return self.countries.get((raw or "").lower().strip().rstrip("."))

    def location(self, city: str | None, country: str | None) -> dict | None:
        c = (city or "").lower().strip()
        for loc in self.locations:
            if (country is None or loc["country"] == country) and c in [loc["city"].lower(), *loc["aliases"]]:
                return loc
        return None


# ------------------------------------------------------------------ normalisation

def precision_m2(value_text: str, unit: str) -> float:
    """Half a unit of the last stated digit, in m2: '6.2 ha' is +/- 500 m2."""
    m = re.search(r"\d[\d,]*(?:\.(\d+))?", value_text or "")
    decimals = len(m.group(1)) if m and m.group(1) else 0
    return 0.5 * 10 ** -decimals * TO_M2.get(unit, 1.0)


def digits(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def number_in_text(number: float, *texts: str) -> bool:
    n = f"{number:f}".rstrip("0").rstrip(".")
    return digits(n) in "".join(digits(t) for t in texts)


def normalize(f: Field, c: Candidate, ref: Reference, country: str | None) -> list[Flag]:
    v = c.value
    flags: list[Flag] = []
    if f.kind == "area":
        if v.get("number") is None or v.get("unit") not in TO_M2:
            c.status = "rejected"
            return [Flag("area_unreadable", "warn", f"Could not read an area from '{v['value_text']}'", f.name, c.field_value_id)]
        if not number_in_text(v["number"], v["value_text"], c.value.get("quote", "")):
            flags.append(Flag("number_not_in_source", "warn", f"{v['number']} does not appear in the cited text",
                              f.name, c.field_value_id))
        c.normalized = round(v["number"] * TO_M2[v["unit"]], 1)
        if v["unit"] != "m2":
            c.notes.append(f"converted from {v['value_text']}")
    elif f.kind == "integer":
        if v.get("number") is None:
            c.status = "rejected"
        else:
            c.normalized = int(v["number"])
    elif f.kind in ("vocab", "vocab_list"):
        codes = ref.vocab(f.vocab, v["value_text"], country)
        suggestion = v.get("normalized_text")
        if len(codes) == 1:
            c.normalized = codes.pop()
        elif len(codes) > 1:
            c.normalized = suggestion if suggestion in codes else sorted(codes)[0]
            flags.append(Flag("ambiguous_term", "info", f"'{v['value_text']}' could mean {', '.join(sorted(codes))}",
                              f.name, c.field_value_id))
        elif suggestion in ref.codes[f.vocab]:
            c.normalized = suggestion
            c.notes.append("mapped by AI suggestion")
            flags.append(Flag("term_not_in_synonyms", "info",
                              f"'{v['value_text']}' mapped to {suggestion} by the AI; not in the workspace's synonym list",
                              f.name, c.field_value_id, {"term": v["value_text"], "code": suggestion}))
        else:
            c.status = "rejected"
            flags.append(Flag("unmapped_term", "warn",
                              f"'{v['value_text']}' has no place in the workspace's {f.vocab} list", f.name, c.field_value_id,
                              {"term": v["value_text"]}))
    elif f.kind == "country":
        code = ref.country(v.get("normalized_text")) or ref.country(v["value_text"])
        c.normalized = code or (v.get("normalized_text") or v["value_text"]).strip()
        if not code:
            flags.append(Flag("unknown_country", "warn", f"'{v['value_text']}' is not a market in the reference data",
                              f.name, c.field_value_id))
    elif f.kind == "city":
        c.normalized = v["value_text"].strip().rstrip(".")
        loc = ref.location(c.normalized, country)
        if loc:
            c.normalized = loc["city"]
    elif f.kind == "datetime":
        try:
            dt = datetime.fromisoformat(v.get("normalized_text") or "")
            c.normalized = dt.isoformat()
            if dt.tzinfo is None:
                flags.append(Flag("no_time_zone", "info", "Deadline has no time zone", f.name, c.field_value_id))
        except ValueError:
            c.status = "rejected"
            flags.append(Flag("date_unreadable", "warn", f"Could not read a date from '{v['value_text']}'", f.name,
                              c.field_value_id))
    elif f.kind == "date":
        try:
            c.normalized = date.fromisoformat((v.get("normalized_text") or "")[:10]).isoformat()
        except ValueError:
            c.status = "rejected"
    elif f.kind == "enum":
        if v.get("normalized_text") in f.options:
            c.normalized = v["normalized_text"]
        else:
            c.status = "rejected"
    else:
        c.normalized = v["value_text"].strip()
    return flags


# ------------------------------------------------------------------ consolidation

def priority(f: Field, c: Candidate) -> tuple:
    order = SOURCE_PRIORITY.get("area" if f.kind == "area" else f.name, SOURCE_PRIORITY["default"])
    rank = order.index(c.doc_class) if c.doc_class in order else len(order)
    return (rank, -c.confidence, c.field_value_id)


def same(f: Field, a: Candidate, b: Candidate) -> bool:
    if f.kind == "area":
        tol = max(0.005 * max(a.normalized, b.normalized),
                  precision_m2(a.value["value_text"], a.value["unit"]) + precision_m2(b.value["value_text"], b.value["unit"]))
        return abs(a.normalized - b.normalized) <= tol
    if f.kind in ("text", "text_list"):
        return fuzz.token_set_ratio(str(a.normalized), str(b.normalized)) >= 88
    if f.kind == "city":
        return str(a.normalized).lower() == str(b.normalized).lower()
    return a.normalized == b.normalized


def group(f: Field, cands: list[Candidate]) -> list[list[Candidate]]:
    groups: list[list[Candidate]] = []
    for c in sorted(cands, key=lambda c: priority(f, c)):
        for g in groups:
            if same(f, g[0], c):
                g.append(c)
                break
        else:
            groups.append([c])
    return groups


def consolidate(f: Field, cands: list[Candidate], doc_names: dict[int, str]) -> tuple[list[Candidate], dict | None]:
    """Mark the winning candidate(s) proposed and the rest superseded. Returns (chosen, conflict)."""
    live = [c for c in cands if c.status != "rejected"]
    groups = group(f, live)
    chosen = []
    for g in groups:
        g[0].status = "proposed"
        g[0].notes.append(f"stated in {len(g)} place(s)" if len(g) > 1 else "")
        for other in g[1:]:
            other.status = "superseded"
        chosen.append(g[0])
    if f.is_list or len(groups) <= 1:
        return chosen, None
    summary = "; ".join(f"{doc_names.get(g[0].document_id, 'unknown')} states {g[0].value['value_text']}" for g in groups)
    return chosen, {"field": f.name, "ids": [g[0].field_value_id for g in groups], "summary": summary}


# ------------------------------------------------------------------ engine run

def run_engine(envelope_id: int, trace_id: uuid.UUID | None = None) -> None:
    with engine.begin() as conn:
        run_id = conn.execute(
            text("""INSERT INTO intake.extraction_run (envelope_id, kind, engine_version, schema_version,
                    dataset_version, trace_id) VALUES (:e, 'engine', :ev, :sv, :dv, :t) RETURNING run_id"""),
            dict(e=envelope_id, ev=ENGINE_VERSION, sv=SCHEMA_VERSION, dv=settings.dataset_version,
                 t=str(trace_id or uuid.uuid4())),
        ).scalar_one()
        Engine(conn, envelope_id, run_id).run()
        conn.execute(text("UPDATE intake.extraction_run SET status = 'succeeded', finished_at = now() WHERE run_id = :r"),
                     dict(r=run_id))


class Engine:
    def __init__(self, conn: Connection, envelope_id: int, run_id: int):
        self.conn = conn
        self.envelope_id = envelope_id
        self.run_id = run_id
        self.ref = Reference(conn)
        self.fields = fields()
        self.flags: list[Flag] = []
        self.record: dict[str, list[Candidate]] = {}

    def run(self) -> None:
        conn, e = self.conn, self.envelope_id
        conn.execute(text("DELETE FROM intake.engine_flag WHERE envelope_id = :e"), dict(e=e))
        conn.execute(text("DELETE FROM intake.field_conflict WHERE envelope_id = :e AND status = 'open'"), dict(e=e))
        conn.execute(text("""DELETE FROM lineage.field_value fv WHERE envelope_id = :e AND origin = 'engine'
                             AND NOT EXISTS (SELECT 1 FROM lineage.field_change c
                                             WHERE fv.field_value_id IN (c.old_field_value_id, c.new_field_value_id))"""),
                     dict(e=e))
        doc_rows = conn.execute(text("SELECT document_id, doc_class, file_name FROM lineage.source_document "
                                     "WHERE envelope_id = :e"), dict(e=e)).all()
        doc_class = {d: c for d, c, _ in doc_rows}
        doc_names = {d: n for d, _, n in doc_rows}
        rows = conn.execute(text("""SELECT field_value_id, field, value, quote, document_id, confidence, origin
                                    FROM lineage.field_value WHERE envelope_id = :e AND origin IN ('ai', 'human')"""),
                            dict(e=e)).all()
        human = {r.field for r in rows if r.origin == "human"}
        by_field: dict[str, list[Candidate]] = defaultdict(list)
        for r in rows:
            if r.origin != "ai" or r.field in human or r.field not in self.fields:
                continue
            by_field[r.field].append(Candidate(r.field_value_id, r.field, dict(r.value, quote=r.quote), r.document_id,
                                               doc_class.get(r.document_id), float(r.confidence or 0.5)))
        self.candidates = by_field

        # country first: it scopes synonyms and city lookup
        country = None
        for name in ["country", *[n for n in self.fields if n != "country"]]:
            f = self.fields[name]
            cands = by_field.get(name, [])
            for c in cands:
                self.flags += normalize(f, c, self.ref, country)
            chosen, conflict = consolidate(f, cands, doc_names)
            self.record[name] = chosen
            if conflict and name not in human:
                self.add_conflict(conflict)
            for c in cands:
                self.save_candidate(c)
            if name == "country" and chosen:
                country = chosen[0].normalized if chosen[0].normalized in self.ref.country_rows else None

        self.apply_human(human)
        self.enrich(country)
        self.write_flags()

    # ---------------------------------------------------------------- helpers

    def value(self, name: str):
        chosen = self.record.get(name) or []
        if not chosen:
            return None
        if self.fields.get(name) and self.fields[name].is_list:
            return [c.normalized for c in chosen]
        return chosen[0].normalized

    def apply_human(self, human: set[str]) -> None:
        """A person's edit wins over machine output for that field."""
        for name in human:
            rows = self.conn.execute(text("""SELECT field_value_id, value, normalized FROM lineage.field_value
                                             WHERE envelope_id = :e AND field = :f AND origin = 'human'
                                             AND status IN ('proposed', 'confirmed') ORDER BY field_value_id"""),
                                     dict(e=self.envelope_id, f=name)).all()
            self.record[name] = [Candidate(r.field_value_id, name, r.value, None, None, 1.0, r.normalized["value"])
                                 for r in rows if r.normalized and r.normalized.get("value") not in (None, "")]

    def save_candidate(self, c: Candidate) -> None:
        self.conn.execute(
            text("UPDATE lineage.field_value SET normalized = CAST(:n AS jsonb), status = :s WHERE field_value_id = :i"),
            dict(i=c.field_value_id, s=c.status,
                 n=json.dumps({"value": c.normalized, "notes": [n for n in c.notes if n]})),
        )

    def add_conflict(self, conflict: dict) -> None:
        self.conn.execute(
            text("INSERT INTO intake.field_conflict (envelope_id, field, field_value_ids, summary) VALUES (:e, :f, :ids, :s)"),
            dict(e=self.envelope_id, f=conflict["field"], ids=conflict["ids"], s=conflict["summary"]))
        self.flags.append(Flag("conflict", "block", conflict["summary"], conflict["field"], conflict["ids"][0]))

    def add_engine_value(self, name: str, value, kind: str = "fact", evidence: dict | None = None,
                         confidence: float = 1.0) -> None:
        self.conn.execute(
            text("""INSERT INTO lineage.field_value (envelope_id, run_id, field, value, normalized, confidence, origin,
                    kind, status) VALUES (:e, :r, :f, CAST(:v AS jsonb), CAST(:n AS jsonb), :c, 'engine', :k, 'proposed')"""),
            dict(e=self.envelope_id, r=self.run_id, f=name, c=confidence, k=kind,
                 v=json.dumps({"value_text": str(value), "evidence": evidence or {}}),
                 n=json.dumps({"value": value, "notes": []})))

    def write_flags(self) -> None:
        if self.flags:
            self.conn.execute(
                text("""INSERT INTO intake.engine_flag (envelope_id, run_id, field_value_id, field, rule, severity,
                        message, evidence) VALUES (:e, :r, :fv, :f, :rule, :sev, :msg, CAST(:ev AS jsonb))"""),
                [dict(e=self.envelope_id, r=self.run_id, fv=fl.field_value_id, f=fl.field, rule=fl.rule,
                      sev=fl.severity, msg=fl.message, ev=json.dumps(fl.evidence) if fl.evidence else None)
                 for fl in self.flags])

    # ---------------------------------------------------------------- enrichment

    def archive(self) -> list[dict]:
        return [{k: float(v) if isinstance(v, Decimal) else v for k, v in r._mapping.items()}
                for r in self.conn.execute(text("""
            SELECT p.proposal_id, p.reference, p.title, p.typology, p.gfa_m2, p.site_area_m2, p.fitout_area_m2,
                   p.landscape_area_m2, p.units, p.keys, p.currency, l.country, l.city, p.studio_code,
                   p.fee_total_local, p.fee_total_usd, p.status, p.loss_reason, p.client_id, c.name AS client_name,
                   array(SELECT service FROM archive.proposal_service s WHERE s.proposal_id = p.proposal_id) AS services
            FROM archive.proposal p JOIN ref.location l USING (location_id) LEFT JOIN archive.client c USING (client_id)
            WHERE p.dataset_version = :v AND p.data_origin <> 'rfp_new'
              -- only history the practice had when the RFP arrived
              AND p.submission_date < (SELECT received_at FROM intake.envelope WHERE envelope_id = :e)::date"""), dict(v=settings.dataset_version, e=self.envelope_id))]

    def enrich(self, country: str | None) -> None:
        archive = self.archive()
        city = self.value("city")
        if country is None and city:
            loc = self.ref.location(city, None)
            if loc:
                country = loc["country"]
                self.add_engine_value("country", country, evidence={"basis": f"inferred from city {loc['city']}"})
        loc = self.ref.location(city, country) if city else None
        if loc:
            self.add_engine_value("location_tier", loc["tier"], evidence={"basis": f"{loc['city']} is a {loc['tier']} location"})
        elif city:
            self.flags.append(Flag("unknown_location_tier", "warn", f"No location tier for {city}", "city"))

        currency = studio = None
        if country:
            in_country = [p for p in archive if p["country"] == country]
            national = self.ref.country_rows.get(country, {}).get("currency")
            if in_country:
                cur, n = Counter(p["currency"] for p in in_country).most_common(1)[0]
                currency = cur
                basis = f"{n} of {len(in_country)} past proposals in this country were billed in {cur}"
            else:
                currency, basis = national, "national currency; no past proposals in this country"
            if currency:
                self.add_engine_value("billing_currency", currency, evidence={"basis": basis, "national": national})
            studio, basis = self.route_studio(country, archive)
            if studio:
                self.add_engine_value("routed_studio", studio, evidence={"basis": basis})

        self.match_client(archive)
        self.plausibility(archive)
        self.suggest_gaps(archive)
        completeness = self.completeness()
        lead = self.qualify(country, completeness)
        deadline = self.value("submission_deadline")
        self.conn.execute(
            text("""UPDATE intake.envelope SET country = :c, location_id = :l, billing_currency = :cur,
                    routed_studio = :s, completeness = :comp, lead_status = :lead, submission_deadline = :d,
                    title = COALESCE(title, :title), status = 'review', updated_at = now() WHERE envelope_id = :e"""),
            dict(e=self.envelope_id, c=country if country in self.ref.country_rows else None,
                 l=loc["location_id"] if loc else None, cur=currency, s=studio, comp=completeness, lead=lead,
                 d=deadline, title=self.value("project_name")))

    def route_studio(self, country: str, archive: list[dict]) -> tuple[str | None, str]:
        past = Counter(p["studio_code"] for p in archive if p["country"] == country)
        if past:
            code, n = past.most_common(1)[0]
            return code, f"{code} wrote {n} of {sum(past.values())} past proposals for this country"
        local = [s for s in self.ref.studios if s["country"] == country]
        if local:
            return local[0]["code"], f"{local[0]['name']} is in this country"
        region = self.ref.country_rows.get(country, {}).get("region")
        same_region = [s for s in self.ref.studios if self.ref.country_rows.get(s["country"], {}).get("region") == region]
        if same_region:
            return same_region[0]["code"], f"{same_region[0]['name']} covers the {region} region"
        return None, ""

    def match_client(self, archive: list[dict]) -> None:
        name = self.value("client_name")
        if not name:
            return
        clean = lambda s: re.sub(r"\s+", " ", re.sub(LEGAL_SUFFIXES, "", s.lower())).strip(" .,")  # noqa: E731
        clients = {(p["client_id"], p["client_name"]) for p in archive if p["client_name"]}
        scored = sorted(((fuzz.token_sort_ratio(clean(name), clean(cn)), cid, cn) for cid, cn in clients), reverse=True)
        if scored and scored[0][0] >= 90:
            score, cid, cn = scored[0]
            past = [p["reference"] for p in archive if p["client_id"] == cid]
            self.add_engine_value("client_status", "repeat",
                                  evidence={"client_id": cid, "archive_name": cn, "score": score, "past_proposals": past})
        else:
            near = scored[0] if scored and scored[0][0] >= 75 else None
            self.add_engine_value("client_status", "new",
                                  evidence={"nearest": near[2], "score": near[0]} if near else {})

    def plausibility(self, archive: list[dict]) -> None:
        typology = self.value("typology")
        checks = [
            ("plot_ratio", "GFA / site area", "gfa_m2", "site_area_m2"),
            ("fitout_share", "interior area / GFA", "fitout_area_m2", "gfa_m2"),
            ("landscape_share", "landscape area / site area", "landscape_area_m2", "site_area_m2"),
            ("area_per_unit", "GFA per residential unit (m²)", "gfa_m2", "units"),
            ("area_per_key", "GFA per hotel key (m²)", "gfa_m2", "keys"),
        ]
        for rule, label, num, den in checks:
            a, b = self.value(num), self.value(den)
            if not a or not b:
                continue
            ratio = a / b
            pool = [p for p in archive if p.get(num) and p.get(den) and p["typology"] == typology]
            scope = typology
            if len(pool) < 3:
                pool = [p for p in archive if p.get(num) and p.get(den)]
                scope = "all typologies"
            if len(pool) < 2:
                continue
            ratios = sorted(p[num] / p[den] for p in pool)
            lo, hi = ratios[0], ratios[-1]
            if ratio < lo * 0.67 or ratio > hi * 1.5:
                self.flags.append(Flag(
                    rule, "warn", f"{label} is {ratio:,.2f}; the archive range for {scope} is {lo:,.2f}–{hi:,.2f}",
                    num, self.record[num][0].field_value_id if self.record.get(num) else None,
                    {"value": round(ratio, 3), "range": [round(lo, 3), round(hi, 3)], "scope": scope,
                     "cases": [p["reference"] for p in pool]}))

    def suggest_gaps(self, archive: list[dict]) -> None:
        typology, services = self.value("typology"), self.value("services") or []
        gfa, site = self.value("gfa_m2"), self.value("site_area_m2")

        def suggest(target: str, num: str, den: str, base: float | None, reason: str) -> None:
            if self.value(target) or not base:
                return
            pool = [p for p in archive if p.get(num) and p.get(den) and p["typology"] == typology] or \
                   [p for p in archive if p.get(num) and p.get(den)]
            if len(pool) < 2:
                return
            ratio = statistics.median(p[num] / p[den] for p in pool)
            value = round(base * ratio, -2)
            self.add_engine_value(target, value, kind="suggestion", confidence=0.4, evidence={
                "basis": f"{reason}: median ratio {ratio:.2f} across {len(pool)} past proposals",
                "cases": [p["reference"] for p in pool]})
            self.flags.append(Flag("gap_suggestion", "info",
                                   f"{self.fields[target].label} not stated; suggested {value:,.0f} m² from similar projects",
                                   target))

        if "interior_design" in services:
            suggest("fitout_area_m2", "fitout_area_m2", "gfa_m2", gfa, "interior area / GFA")
        if "landscape" in services:
            suggest("landscape_area_m2", "landscape_area_m2", "site_area_m2", site, "landscape area / site area")
        if typology and typology != "masterplan":
            suggest("gfa_m2", "gfa_m2", "site_area_m2", site, "GFA / site area")

    def required_fields(self) -> dict[str, float]:
        req = {f: w for f, w in self.ref.criticality.items() if not f.endswith("_m2")}
        req["site_area_m2"] = self.ref.criticality.get("site_area_m2", 0.8)
        for service in self.value("services") or []:
            area = SERVICE_AREA.get(service)
            if area:
                req[area] = self.ref.criticality.get(area, 1.0)
        if self.value("typology") == "masterplan":
            req.pop("gfa_m2", None)
        return req

    def completeness(self) -> float:
        req = self.required_fields()
        present = sum(w for f, w in req.items() if self.value(f) not in (None, [], ""))
        missing = [self.fields[f].label for f in req if self.value(f) in (None, [], "")]
        if missing:
            self.flags.append(Flag("missing_critical", "warn", "Missing pricing-critical fields: " + ", ".join(missing)))
        return round(present / sum(req.values()), 3) if req else 0.0

    def qualify(self, country: str | None, completeness: float) -> str:
        received = self.conn.execute(text("SELECT received_at FROM intake.envelope WHERE envelope_id = :e"),
                                     dict(e=self.envelope_id)).scalar_one()
        effects = []
        for rule in self.rules_triggered(country, completeness, received):
            effects.append(rule["effect"])
            self.flags.append(Flag(f"rule:{rule['name']}", "block" if rule["effect"] == "recommend_no_bid" else "warn",
                                   rule["message"], rule["expression"].get("field")))
        if "recommend_no_bid" in effects:
            return "recommend_no_bid"
        if "needs_info" in effects:
            return "needs_info"
        return "qualified"

    @property
    def rules(self):
        return self.ref.rules

    def rules_triggered(self, country, completeness, received):
        for rule in self.rules:
            ex = rule["expression"]
            name = ex.get("field")
            value = completeness if name == "completeness" else self.value(name)
            op = ex["op"]
            hit = False
            if op == "days_until_lt" and value:
                days = (datetime.fromisoformat(value) - received).total_seconds() / 86400
                hit = days < ex["value"]
            elif op == "not_in_ref" and name == "country":
                hit = value is not None and value not in self.ref.country_rows
            elif op == "empty":
                hit = not value and bool(self.candidates.get(name))
            elif op == "lt":
                hit = value is not None and value < ex["value"]
            elif op == "matches" and value:
                hit = re.search(ex["pattern"], str(value), re.I) is not None
            if hit:
                yield rule


# ------------------------------------------------------------------ comparables

def comparables(conn: Connection, envelope_id: int, limit: int = 3) -> dict:
    """Closest past proposals, fees shown in the RFP's billing currency. References, not a quote."""
    env = conn.execute(text("SELECT country, billing_currency FROM intake.envelope WHERE envelope_id = :e"),
                       dict(e=envelope_id)).one()
    vals = {r.field: r.normalized["value"] for r in conn.execute(text("""
        SELECT DISTINCT ON (field) field, normalized FROM lineage.field_value
        WHERE envelope_id = :e AND status IN ('proposed', 'confirmed') AND kind = 'fact' AND normalized IS NOT NULL
        ORDER BY field, (origin = 'human') DESC, field_value_id"""), dict(e=envelope_id))}
    region = conn.execute(text("SELECT region FROM ref.country WHERE code = :c"), dict(c=env.country)).scalar()
    fx = {c: float(r) for c, r in conn.execute(text(
        "SELECT DISTINCT ON (currency) currency, per_usd FROM ref.fx_rate ORDER BY currency, as_of DESC"))}
    fx_date = conn.execute(text("SELECT max(as_of) FROM ref.fx_rate")).scalar()
    target = env.billing_currency or "USD"
    size_field = "site_area_m2" if vals.get("typology") == "masterplan" else "gfa_m2"
    size = vals.get(size_field)
    rows = conn.execute(text("""
        SELECT p.reference, p.title, p.typology, p.gfa_m2, p.site_area_m2, p.currency, p.fee_total_local,
               p.fee_total_usd, p.status, p.loss_reason, l.country, l.city, c.region, p.submission_date,
               array(SELECT service FROM archive.proposal_service s WHERE s.proposal_id = p.proposal_id) AS services
        FROM archive.proposal p JOIN ref.location l USING (location_id) JOIN ref.country c ON c.code = l.country
        WHERE p.dataset_version = :v AND p.data_origin <> 'rfp_new'
          AND p.submission_date < (SELECT received_at FROM intake.envelope WHERE envelope_id = :e)::date"""), dict(v=settings.dataset_version, e=envelope_id)).all()
    scored = []
    for p in rows:
        score = 3.0 * (p.typology == vals.get("typology")) + 2.0 * (p.country == env.country) + 1.0 * (p.region == region)
        p_size = p.site_area_m2 if size_field == "site_area_m2" else p.gfa_m2
        if size and p_size:
            score -= abs(math.log(float(size) / float(p_size)))
        services = set(vals.get("services") or [])
        if services:
            score += len(services & set(p.services)) / len(services)
        fee_target = float(p.fee_total_usd) * fx.get(target, 1.0) if p.fee_total_usd else None
        per_m2 = fee_target / float(p_size) if fee_target and p_size else None
        scored.append((score, dict(reference=p.reference, title=p.title, city=p.city, country=p.country,
                                   typology=p.typology, services=sorted(p.services), size_m2=float(p_size or 0),
                                   size_basis=size_field, fee_local=float(p.fee_total_local or 0), currency=p.currency,
                                   fee_in_target=round(fee_target) if fee_target else None,
                                   fee_per_m2_in_target=round(per_m2, 2) if per_m2 else None, status=p.status,
                                   loss_reason=p.loss_reason, year=p.submission_date.year if p.submission_date else None,
                                   score=round(score, 2))))
    scored.sort(key=lambda s: -s[0])
    return {"currency": target, "fx_as_of": fx_date.isoformat() if fx_date else None,
            "fx_note": "AED/SAR/QAR/BHD/OMR/JOD are official pegs; other rates are illustrative",
            "label": "Reference projects from the archive. Not a fee quote.",
            "items": [s[1] for s in scored[:limit]]}
