"""The pricing logic register: every rule and number the fee engine and gate 1 use, in the order they are
applied, each with its value, where it came from and when it last changed. Read-only; values are edited
under Set up, and each edit lands in ref.setting_change, which this register shows.

With an envelope id, it also marks the entries that request's fee used, so a fee card line can be traced
back to the register.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import Connection, text

from bidwright import feecard, fees, gate1
from bidwright.auth import require
from bidwright.config import settings
from bidwright.db import engine
from bidwright.schema import fields as schema_fields
from bidwright.tenancy import Principal

router = APIRouter(prefix="/api")
READ = Depends(require("read"))

# Rules that live in code rather than settings. They are part of the register so nothing that moves a fee is
# hidden; changing one is a code change, versioned with the engine.
BUILT_IN = {
    "built_in.cost_fallback": ("Construction cost when a city has no figure",
                               "Use the city's figure for the building type; else the country-wide figure; else the "
                               "place's average cost x the building type's cost ratio.", "Fee engine"),
    "built_in.escalation": ("Construction cost to today's prices",
                            "Escalate the cost book figure from its price year to the year of the request at the "
                            "market's escalation rate.", "Fee engine"),
    "built_in.won_range": ("Won-bid range",
                           "Price each past won bid with today's method, and show the spread of what was actually "
                           "quoted against it (needs two or more won bids). Lost bids show where losing started.",
                           "Fee engine"),
    "built_in.swings": ("Fee range on the card",
                        "Price every assumed area at its low and high estimate; combine those swings with the "
                        "won-bid range by root-sum-of-squares around the opening fee.", "Fee card"),
    "built_in.confidence": ("Confidence of an estimate",
                            "High: 4 or more comparables and high/low within 1.5x. Medium: 2 or more and within "
                            "2.5x. Otherwise low.", "Gate 1"),
    "built_in.estimate_scope": ("Which comparables an estimate uses",
                                "A project the client names first; else past projects of the same kind in the same "
                                "country, then region, then all markets.", "Gate 1"),
    "built_in.currency": ("Billing currency",
                          "The currency the client asks for; else the one the practice has used in that market "
                          "(its archive); else the market's national currency.", "Gate 1"),
}

SECTIONS = [
    ("cost", "1. Construction cost", "What the building costs per m², by market and type. Architecture and "
                                     "interior fees are a share of it."),
    ("pct", "2. Fee as a share of cost", "The full-service architecture fee by use, before size and market."),
    ("adjust", "3. Adjustments", "Market, location, client and complexity multipliers."),
    ("stages", "4. Splitting by stage and service", "How much of the full fee each stage carries, and how "
                                                    "interior, landscape and masterplan are priced."),
    ("range", "5. The range", "How the card shows how far the fee could move."),
    ("terms", "6. Expenses, payment and programme", "Renders, trips, payment terms and stage durations."),
    ("gate1", "7. Gate 1: qualify, clarify or decline", "The rules that read a request before it is priced, and "
                                                       "how gaps are estimated."),
]

PREFIX = [  # fee assumption key prefix -> (section, title template)
    ("full_service_fee_pct.", "pct", "Full-service fee: {x}"),
    ("scale_exponent", "pct", "Size scaling"),
    ("modifier.location_tier.", "adjust", "Second-tier city"),
    ("modifier.client.", "adjust", "Repeat client"),
    ("modifier.complexity.", "adjust", "Complexity: {x}"),
    ("stage_share.", "stages", "Stage share: {x}"),
    ("interior.", "stages", "Interior: {x}"),
    ("landscape.", "stages", "Landscape: {x}"),
    ("masterplan.", "stages", "Masterplan: {x}"),
    ("render.", "terms", "Renders: {x}"),
    ("trip.", "terms", "Site trips: {x}"),
    ("payment.", "terms", "Payment: {x}"),
    ("terms.", "terms", "Terms: {x}"),
    ("programme.weeks.", "terms", "Programme: {x}"),
]

POLICY_TITLES = {"stage_defaults": "Stages assumed when none are named", "service_default": "Service assumed when none is named",
                 "min_comparables": "Comparables needed to estimate", "max_questions": "Questions per message to the client",
                 "question_order": "Which gaps are asked about first", "decline_reasons": "Reasons offered when declining"}
EFFECT = {"recommend_no_bid": "Decline", "needs_info": "Clarify", "flag": "Flag"}


NAMES = {"advance_pct": "advance on signing", "payment_days": "days to pay an invoice", "validity_days": "how long a proposal is valid",
         "default_qty": "how many when no similar project", "unit_price.high_cost": "price, high-cost market",
         "unit_price.low_cost": "price, low-cost market", "long_haul": "long-haul trip", "regional": "regional trip",
         "fee_pct": "fee % of its cost", "stage_share_to_dd": "share priced up to Design Development",
         "rate_per_ha": "rate per hectare, first 50 ha", "rate_per_ha_above_50": "rate per hectare above 50 ha",
         "band_factor.low_cost": "low-cost market factor", "cost_per_m2.high_cost": "cost per m², high-cost market",
         "cost_per_m2.low_cost": "cost per m², low-cost market"}


def words(code: str, labels: dict[str, str]) -> str:
    if code in labels:
        return labels[code]
    if code in NAMES:
        return NAMES[code]
    head, _, tail = code.rpartition(".")
    if head and tail in labels:  # fitout_cost_ratio.office -> fitout cost ratio · Office
        return f"{head.replace('_', ' ')} · {labels[tail]}"
    return code.replace("_", " ").replace(".", " · ")


def fmt(value, unit: str | None) -> str:
    if value is None:
        return "—"
    if isinstance(value, (int, float)):
        n = float(value)
        u = (unit or "").strip()
        if u.startswith("share of"):
            return f"{n:.0%} {u}"
        if u == "multiplier":
            return f"× {n:g}"
        if u.startswith("%"):
            return f"{n:g}{u}"
        return f"{n:,.0f} {u}".strip() if n >= 100 else f"{n:g} {u}".strip()
    return str(value)


def changes(conn: Connection) -> dict[tuple[str, str], dict]:
    out: dict[tuple[str, str], dict] = {}
    for r in conn.execute(text("SELECT setting, key, old, new, reason, changed_by, changed_at FROM ref.setting_change "
                               "ORDER BY changed_at")):
        k = (r.setting, r.key.split("/")[0] if r.setting == "construction_cost" else r.key)
        out[k] = dict(by=r.changed_by, at=r.changed_at, reason=r.reason, old=r.old, new=r.new)
    return out


def entries(conn: Connection) -> list[dict]:
    labels = dict(conn.execute(text("SELECT code, label FROM ref.stage UNION ALL SELECT code, label FROM ref.typology "
                                    "UNION ALL SELECT code, label FROM ref.service")).all())
    labels |= {n: f.label for n, f in schema_fields().items()}
    changed = changes(conn)
    out: list[dict] = []

    def add(section: str, id_: str, title: str, value: str, rule: str, source: str, kind: str,
            change: tuple[str, str] | None = None, edit: str | None = None) -> None:
        out.append(dict(id=id_, section=section, title=title, value=value, rule=rule, source=source, kind=kind,
                        changed=changed.get(change) if change else None, edit=edit))

    # 1. construction cost: one line per market, then the type ratios and the built-in fallbacks
    names = dict(conn.execute(text("SELECT code, name FROM ref.country")).all())
    markets = {r.country: r for r in conn.execute(text("SELECT * FROM ref.market"))}
    for country, n, avg, year, src in conn.execute(text("""
            SELECT country, count(*), max(usd_m2) FILTER (WHERE cost_type = 'average' AND city = ''),
                   max(price_year), string_agg(DISTINCT source, '; ')
            FROM ref.construction_cost GROUP BY country ORDER BY country""")):
        m = markets.get(country)
        esc = f"; escalates {float(m.escalation_pct):g}% a year" if m else ""
        add("cost", f"cost.{country}", f"Cost book: {names.get(country, country)}",
            (f"USD {float(avg):,.0f}/m² average" if avg else f"{n} figures") + f" ({year} prices{esc})",
            f"{n} figures by city and building type.",
            "Synthetic demo figures" if "synthetic" in (src or "") else "Cost book", "cost",
            ("construction_cost", country), "#/settings")
    for cost_type, ratio in conn.execute(text("SELECT cost_type, ratio FROM ref.typology_cost_ratio ORDER BY cost_type")):
        add("cost", f"ratio.{cost_type}", f"Cost ratio: {words(cost_type, labels)}", f"× {float(ratio):g} of average",
            "Used when a place has no figure for this building type.", "Starter pack", "setting")
    for k in ("built_in.cost_fallback", "built_in.escalation"):
        t, rule, where = BUILT_IN[k]
        add("cost", k, t, "Built in", rule, f"{where} ({fees.FEE_ENGINE_VERSION})", "built_in")

    # 2-4, 6. the fee method
    for key, value_text, value, unit, rationale in conn.execute(text(
            "SELECT key, value_text, value, unit, rationale FROM ref.fee_assumption ORDER BY key")):
        for prefix, section, title in PREFIX:
            if key.startswith(prefix):
                rest = key[len(prefix):]
                add(section, key, title.format(x=words(rest, labels)),
                    fmt(float(value) if value is not None else value_text, unit), "",
                    rationale or "Starter pack", "setting", ("fee_assumption", key), "#/settings")
                break

    # 3. market factors
    for c, m in sorted(markets.items(), key=lambda t: names.get(t[0], t[0])):
        add("adjust", f"market.{c}", f"Market: {names.get(c, c)}", f"× {float(m.fee_factor):g} · {m.band}-cost band",
            "Multiplies the fee % for this market; the band picks landscape, render and masterplan rates.",
            m.fee_factor_source or "Market setting", "market", ("market", c), "#/settings")

    # 5. the range
    for k in ("built_in.won_range", "built_in.swings"):
        t, rule, where = BUILT_IN[k]
        add("range", k, t, "Built in", rule, where, "built_in")

    # 7. gate 1
    for r in conn.execute(text("SELECT name, expression, effect, message, active FROM ref.qualification_rule "
                               "ORDER BY CASE effect WHEN 'recommend_no_bid' THEN 0 WHEN 'needs_info' THEN 1 ELSE 2 END, name")):
        e = r.expression
        threshold = (f"{e['value']:.0%}" if isinstance(e.get("value"), float) and e["value"] < 1
                     else f"under {e['value']} working days" if e.get("op") == "working_days_lt" else None)
        add("gate1", f"rule.{r.name}", r.message, EFFECT[r.effect] + (f" · {threshold}" if threshold else "")
            + ("" if r.active else " · off"), "", "Practice rule", "rule", ("qualification_rule", r.name))
    for key, value in conn.execute(text("SELECT key, value FROM ref.intake_setting ORDER BY key")):
        if key == "stage_defaults":
            shown = "; ".join(f"{words(k, labels)}: {', '.join(words(s, labels) for s in v)}" for k, v in value.items())
        elif isinstance(value, list):
            shown = ", ".join(words(str(v), labels) for v in value)
        else:
            shown = str(value)
        add("gate1", f"policy.{key}", POLICY_TITLES.get(key, words(key, labels)), shown, "", "Intake policy",
            "policy", ("intake_setting", key))
    for k in ("built_in.currency", "built_in.estimate_scope", "built_in.confidence"):
        t, rule, where = BUILT_IN[k]
        add("gate1", k, t, "Built in", rule, where, "built_in")
    return out


def used_by(conn: Connection, envelope_id: int) -> dict:
    """The register entries one request's fee and gate 1 assessment used."""
    values, assumptions, a = feecard.pricing_values(conn, envelope_id)
    params = fees.Parameters(conn)
    env = conn.execute(text("SELECT received_at, title FROM intake.envelope WHERE envelope_id = :e"),
                       dict(e=envelope_id)).one()
    received = env.received_at or datetime.now(timezone.utc)
    used = {f"rule.{r['name']}" for r in a["rules"]} | {"built_in.currency", "built_in.estimate_scope",
                                                         "built_in.confidence"}
    if assumptions:
        used.add("policy.min_comparables")
    try:
        archive = fees.load_archive(conn, settings.dataset_version, before=received.date())
        est = feecard.price(values, params, received.year, archive)
    except (fees.MissingInputs, fees.PricingError):
        return dict(envelope_id=envelope_id, title=values.get("project_name") or env.title, priced=False,
                    used=sorted(used))
    p = est.inputs
    used |= {f"market.{p.country}", "built_in.swings", "payment.advance_pct", "terms.payment_days",
             "terms.validity_days"}
    if est.range:
        used.add("built_in.won_range")
    _, mods = fees.modifiers(p, params)
    used |= {f.name for f in mods}
    for ln in est.lines:
        used |= {f.name for f in ln.factors if f.name in params.values}
    if "architecture" in p.services:
        key = p.cost_type if p.cost_type and p.cost_type.startswith("residential") else p.typology
        used |= {f"full_service_fee_pct.{key}", "scale_exponent"}
    if {"architecture", "interior_design"} & set(p.services) and not p.construction_cost_usd_m2:
        used |= {f"cost.{p.country}", "built_in.escalation", "built_in.cost_fallback"}
        if not any(params.costs.get((p.country, c, p.cost_type)) for c in (p.city or "", "")):
            used.add(f"ratio.{p.cost_type}")
    used |= {f"programme.weeks.{s}" for s in p.stages if f"programme.weeks.{s}" in params.values}
    if any(asm["field"] == "stage_package" for asm in assumptions):
        used.add("policy.stage_defaults")
    return dict(envelope_id=envelope_id, title=values.get("project_name") or env.title, priced=True,
                currency=est.currency, opening=round(est.fee_total_local), used=sorted(used))


@router.get("/logic")
def register(envelope_id: int | None = None, _: Principal = READ):
    with engine.connect() as conn:
        out = dict(engine_version=fees.FEE_ENGINE_VERSION, questions_prompt=gate1.QUESTIONS_PROMPT,
                   sections=[dict(key=k, title=t, about=a) for k, t, a in SECTIONS], entries=entries(conn),
                   request=None)
        if envelope_id is not None:
            try:
                out["request"] = used_by(conn, envelope_id)
            except HTTPException:
                out["request"] = None
        return out
