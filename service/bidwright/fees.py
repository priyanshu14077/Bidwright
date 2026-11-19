"""Fee engine. Deterministic: no model calls.

Prices a confirmed request the way the practice prices:

1. construction cost per m² for the project's market and use, escalated to the year;
2. a full-service fee % by use, scaled for size and market;
3. modifiers for location tier, client and complexity;
4. stage shares, and separate bases for interior (fit-out cost), landscape
   (landscape cost) and masterplan (rate per hectare).

Every parameter is workspace data, so a firm recalibrates by editing settings,
not code: the fee method (ref.fee_assumption), markets (ref.market) and the
construction cost book (ref.construction_cost). Each line carries the factors it used.
The range comes from the archive: how far the firm's won bids sat from this
same model.

    uv run python -m bidwright.fees            # backtest on the demo workspace's archive
"""

import argparse
import json
import math
import statistics
from dataclasses import asdict, dataclass, field
from datetime import date

from sqlalchemy import Connection, text

FEE_ENGINE_VERSION = "fees-v0"
COST_TYPES = ("average", "residential_highrise", "residential_villa", "office", "mixed_use",
              "hospitality_5star", "hospitality_3star")
TO_DD = ("concept", "schematic_design", "design_development")
INTERIOR_RATIO = {"hospitality": "hospitality", "residential": "residential", "mixed_use": "residential",
                  "office": "office"}
BASIS = {
    "architecture": ("pct_of_construction_cost", "gfa_m2", "m2 GFA"),
    "interior_design": ("pct_of_fitout_cost", "fitout_area_m2", "m2 fit-out"),
    "landscape": ("pct_of_landscape_cost", "landscape_area_m2", "m2 landscape"),
    "masterplan": ("per_hectare", "site_area_m2", "ha"),
}
COMPLEXITY = ("low", "medium", "high")


class MissingInputs(Exception):
    def __init__(self, fields: list[str]):
        super().__init__("cannot price without: " + ", ".join(fields))
        self.fields = fields


class PricingError(Exception):
    pass


def round_sig(x: float, digits: int = 3) -> float:
    if not x:
        return 0.0
    return round(x, digits - 1 - math.floor(math.log10(abs(x))))


@dataclass
class PricingInputs:
    services: list[str]
    stages: list[str]
    country: str
    city: str | None
    location_tier: str
    currency: str
    year: int
    typology: str | None = None
    cost_type: str | None = None
    gfa_m2: float | None = None
    site_area_m2: float | None = None
    fitout_area_m2: float | None = None
    landscape_area_m2: float | None = None
    client_status: str = "new"
    complexity: str = "medium"
    studio: str | None = None
    construction_cost_usd_m2: float | None = None  # an estimator's override of the benchmark
    assumed: list[str] = field(default_factory=list)  # inputs the engine defaulted


@dataclass
class Factor:
    name: str
    value: float | str
    source: str


@dataclass
class FeeLine:
    service: str
    stage: str
    basis: str
    basis_qty: float
    basis_unit: str
    amount_usd: float
    amount_local: float
    factors: list[Factor]


@dataclass
class Reimbursable:
    type: str
    qty: int
    unit_rate_local: float
    amount_local: float
    note: str


@dataclass
class Milestone:
    seq: int
    milestone: str
    stage: str | None
    pct: float
    amount_local: float


@dataclass
class FeeEstimate:
    engine_version: str
    inputs: PricingInputs
    currency: str
    fx_per_usd: float
    lines: list[FeeLine]
    fee_total_local: float
    fee_total_usd: float
    reimbursables: list[Reimbursable]
    reimbursables_total_local: float
    payment_schedule: list[Milestone]
    programme: list[dict]
    deliverables: list[dict]
    factors: list[Factor]
    range: dict | None
    warnings: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------- parameters

class Parameters:
    """The workspace's fee assumptions, cost ratios, FX and vocabulary."""

    def __init__(self, conn: Connection):
        self.values: dict[str, float | str] = {}
        self.rationale: dict[str, str] = {}
        for key, value_text, value, rationale in conn.execute(
                text("SELECT key, value_text, value, rationale FROM ref.fee_assumption")):
            self.values[key] = float(value) if value is not None else value_text
            self.rationale[key] = rationale or ""
        self.cost_ratio = {t: float(r) for t, r in conn.execute(
            text("SELECT cost_type, ratio FROM ref.typology_cost_ratio"))}
        self.fx = {c: float(r) for c, r in conn.execute(text(
            "SELECT DISTINCT ON (currency) currency, per_usd FROM ref.fx_rate ORDER BY currency, as_of DESC"))}
        self.countries: dict[str, str] = {}
        self.regions: dict[str, str] = {}
        self.national_currency: dict[str, str] = {}
        self.country_names: dict[str, str] = {}
        for code, name, region, cur, aliases in conn.execute(
                text("SELECT code, name, region, national_currency, aliases FROM ref.country")):
            self.regions[code] = region
            self.country_names[code] = name
            self.national_currency[code] = cur
            for key in (code, name, *aliases):
                self.countries[key.lower()] = code
        self.stage_labels = dict(conn.execute(text("SELECT code, label FROM ref.stage")).all())
        self.stage_seq = dict(conn.execute(text("SELECT code, seq FROM ref.stage")).all())
        self.service_labels = dict(conn.execute(text("SELECT code, label FROM ref.service")).all())
        self.studio_country = dict(conn.execute(text("SELECT code, country FROM ref.studio")).all())
        self.markets = {r.country: r for r in conn.execute(text("SELECT * FROM ref.market"))}
        self.costs = {(r.country, r.city, r.cost_type): r for r in conn.execute(text(
            "SELECT country, city, cost_type, usd_m2, price_year, basis, source FROM ref.construction_cost"))}
        self.city_names: dict[tuple[str, str], str] = {}
        for country, city, aliases in conn.execute(text("SELECT country, city, aliases FROM ref.location")):
            for name in (city, *aliases):
                self.city_names[(country, name.lower())] = city
        if not self.values:
            raise PricingError("this workspace has no fee method; reseed it from the starter pack")

    def num(self, key: str, default: float | None = None) -> float:
        v = self.values.get(key, default)
        if v is None:
            raise PricingError(f"missing fee assumption {key}")
        return float(v)

    def factor(self, key: str, default: float | None = None) -> Factor:
        return Factor(key, self.num(key, default), "assumption" if key in self.values else "default")

    def market(self, country: str):
        m = self.markets.get(country)
        if m is None:
            raise PricingError(f"no market settings for {country}; add the country under Settings > Pricing")
        return m

    def market_factor(self, country: str) -> Factor:
        m = self.market(country)
        return Factor(f"market.{country}.fee_factor", float(m.fee_factor), m.fee_factor_source or "market setting")

    def band(self, country: str) -> str:
        return self.market(country).band

    def cost_rate(self, country: str, city: str | None, cost_type: str) -> tuple[float, int, str]:
        """USD/m² at its price year, from the city's row, else the country-wide row; a missing cost type
        is that place's average x the typology cost ratio."""
        known = self.city_names.get((country, (city or "").lower()), city or "")
        for place in dict.fromkeys((known, "")):
            label = place or f"{country} country-wide"
            if not place and known:
                label += f" (no {known} figures)"
            row = self.costs.get((country, place, cost_type))
            if row is not None:
                return float(row.usd_m2), row.price_year, f"{label} {cost_type} ({row.basis}: {row.source})"
            avg, ratio = self.costs.get((country, place, "average")), self.cost_ratio.get(cost_type)
            if avg is not None and ratio is not None:
                return (float(avg.usd_m2) * ratio, avg.price_year,
                        f"{label} average ({avg.basis}: {avg.source}) x {cost_type} ratio {ratio}")
        raise PricingError(f"no construction cost for {city or ''} {country}: add one under Settings > Pricing, "
                           "or enter a construction cost per m²")


# ---------------------------------------------------------------- inputs

def default_cost_type(typology: str | None, gfa: float | None, site: float | None) -> tuple[str | None, str | None]:
    """The construction cost category for a use, and a note when it was a guess."""
    if typology == "residential":
        if gfa and site and gfa / site < 1.0:
            return "residential_villa", "residential treated as villas (GFA below the site area)"
        return "residential_highrise", "residential treated as apartments (high rise)"
    if typology == "hospitality":
        return "hospitality_5star", "hospitality priced as 5-star; change it for a 3-star brief"
    if typology in ("office", "mixed_use"):
        return typology, None
    return None, None


def inputs_from_record(record: dict, params: Parameters, year: int, overrides: dict | None = None) -> PricingInputs:
    """A confirmed opportunity record, plus an estimator's choices, as pricing inputs."""
    r = {**record, **{k: v for k, v in (overrides or {}).items() if v not in (None, "")}}
    services = list(r.get("services") or [])
    stages = sorted(r.get("stage_package") or [], key=lambda s: params.stage_seq.get(s, 99))
    country = params.countries.get(str(r.get("country") or "").lower())
    missing = []
    if not services:
        missing.append("services")
    if not stages:
        missing.append("stage_package")
    if not country:
        missing.append("country")
    if not r.get("location_tier"):
        missing.append("location_tier")
    if not r.get("typology") and services != ["masterplan"]:
        missing.append("typology")
    for service in services:
        area = BASIS.get(service, (None, None))[1]
        if area and not r.get(area):
            missing.append(area)
    if missing:
        raise MissingInputs(sorted(set(missing)))

    assumed = []
    cost_type = r.get("cost_type")
    if not cost_type:
        cost_type, note = default_cost_type(r.get("typology"), r.get("gfa_m2"), r.get("site_area_m2"))
        if note:
            assumed.append(note)
    complexity = r.get("complexity")
    if complexity not in COMPLEXITY:
        complexity = "medium"
        assumed.append("complexity assumed medium")
    client_status = r.get("client_status")
    if client_status not in ("new", "repeat"):
        client_status = "new"
        assumed.append("client assumed new")
    currency = r.get("billing_currency") or params.national_currency.get(country) or "USD"
    if currency not in params.fx:
        assumed.append(f"no FX rate for {currency}; priced in USD")
        currency = "USD"
    num = lambda k: float(r[k]) if r.get(k) not in (None, "") else None  # noqa: E731
    return PricingInputs(
        services=services, stages=stages, country=country, city=r.get("city"), location_tier=r["location_tier"],
        currency=currency, year=int(r.get("year") or year), typology=r.get("typology"), cost_type=cost_type,
        gfa_m2=num("gfa_m2"), site_area_m2=num("site_area_m2"), fitout_area_m2=num("fitout_area_m2"),
        landscape_area_m2=num("landscape_area_m2"), client_status=client_status, complexity=complexity,
        studio=r.get("routed_studio"), construction_cost_usd_m2=num("construction_cost_usd_m2"), assumed=assumed)


# ---------------------------------------------------------------- the model

def construction_cost(p: PricingInputs, params: Parameters) -> tuple[float, Factor]:
    if p.construction_cost_usd_m2:
        return p.construction_cost_usd_m2, Factor("construction_cost_usd_m2", p.construction_cost_usd_m2,
                                                  "set by the estimator")
    if not p.cost_type:
        raise PricingError("no construction cost category for this use; enter a construction cost per m²")
    base, price_year, how = params.cost_rate(p.country, p.city, p.cost_type)
    rate = float(params.market(p.country).escalation_pct)
    cost = round(base * (1 + rate / 100) ** (p.year - price_year))
    return cost, Factor("construction_cost_usd_m2", cost,
                        f"{how}; escalated at {rate:g}% p.a. from {price_year} to {p.year}")


def modifiers(p: PricingInputs, params: Parameters) -> tuple[float, list[Factor]]:
    fs = []
    if p.location_tier == "second":
        fs.append(params.factor("modifier.location_tier.second"))
    if p.client_status == "repeat":
        fs.append(params.factor("modifier.client.repeat"))
    if p.complexity in ("low", "high"):
        fs.append(params.factor(f"modifier.complexity.{p.complexity}"))
    return round(math.prod(float(f.value) for f in fs), 3), fs


def estimate(p: PricingInputs, params: Parameters, archive: list[dict] | None = None) -> FeeEstimate:
    warnings: list[str] = []
    fx = params.fx.get(p.currency, 1.0)
    mod, mod_factors = modifiers(p, params)
    mf = params.market_factor(p.country)
    band = params.band(p.country)
    factors = [Factor("modifiers", mod, " x ".join(f"{f.name} {f.value}" for f in mod_factors) or "none apply"),
               Factor("market_band", band, f"market.{p.country}.band")]
    cost = None
    if {"architecture", "interior_design"} & set(p.services):
        cost, cost_factor = construction_cost(p, params)
        factors.insert(0, cost_factor)
    share = {s: params.values.get(f"stage_share.{s}") for s in p.stages}
    to_dd_total = sum(params.num(f"stage_share.{s}") for s in TO_DD)

    lines: list[FeeLine] = []

    def add(service: str, stage: str, qty: float, usd: float, fs: list[Factor]) -> None:
        basis, _, unit = BASIS[service]
        lines.append(FeeLine(service, stage, basis, qty, unit, round(usd, 2), round_sig(usd * fx, 3), fs))

    for service in sorted(p.services, key=lambda s: list(BASIS).index(s) if s in BASIS else 9):
        if service == "architecture":
            key = p.cost_type if p.cost_type and p.cost_type.startswith("residential") else p.typology
            base = params.factor(f"full_service_fee_pct.{key}")
            scale = (p.gfa_m2 / 50000) ** params.num("scale_exponent", 0)
            pct = round(float(base.value) * scale * float(mf.value), 3)
            pct_factor = Factor("architecture_full_service_pct", pct,
                                f"base {base.value}% x scale {scale:.3f} x market {mf.value}")
            for stage in p.stages:
                if share[stage] is None:
                    warnings.append(f"Architecture: no fee share for {params.stage_labels.get(stage, stage)}; price it by hand")
                    continue
                add(service, stage, p.gfa_m2, p.gfa_m2 * cost * pct / 100 * mod * float(share[stage]),
                    [pct_factor, Factor(f"stage_share.{stage}", float(share[stage]), "assumption")])
        elif service == "interior_design":
            ratio = params.factor(f"interior.fitout_cost_ratio.{INTERIOR_RATIO.get(p.typology or '', 'residential')}")
            fit_cost = round(float(ratio.value) * cost)
            fee_pct = params.factor("interior.fee_pct")
            to_dd = params.factor("interior.stage_share_to_dd")
            full = p.fitout_area_m2 * fit_cost * float(fee_pct.value) / 100 * float(mf.value) * mod * float(to_dd.value)
            fs = [Factor("interior_fitout_cost_usd_m2", fit_cost, f"{ratio.value} x building cost"), fee_pct, mf, to_dd]
            for stage in p.stages:
                if stage not in TO_DD:
                    warnings.append(f"Interior design is priced to Design Development; "
                                    f"{params.stage_labels.get(stage, stage)} is not included")
                    continue
                add(service, stage, p.fitout_area_m2, full * float(share[stage]) / to_dd_total, fs)
        elif service == "landscape":
            land_cost = params.factor(f"landscape.cost_per_m2.{band}_cost")
            fee_pct = params.factor("landscape.fee_pct")
            to_dd = params.factor("landscape.stage_share_to_dd")
            full = p.landscape_area_m2 * float(land_cost.value) * float(fee_pct.value) / 100 * mod * float(to_dd.value)
            for stage in p.stages:
                if stage not in TO_DD:
                    warnings.append(f"Landscape is priced to Design Development; "
                                    f"{params.stage_labels.get(stage, stage)} is not included")
                    continue
                add(service, stage, p.landscape_area_m2, full * float(share[stage]) / to_dd_total,
                    [land_cost, fee_pct, to_dd])
        elif service == "masterplan":
            ha = p.site_area_m2 / 10000
            first, above = params.factor("masterplan.rate_per_ha"), params.factor("masterplan.rate_per_ha_above_50")
            base = min(ha, 50) * float(first.value) + max(ha - 50, 0) * float(above.value)
            band_factor = params.factor("masterplan.band_factor.low_cost") if band == "low" else \
                Factor("masterplan.band_factor", 1.0, "high-cost market")
            for stage in p.stages:
                sf = params.values.get(f"masterplan.stage_factor.{stage}")
                if sf is None:
                    warnings.append(f"Masterplan: no rate for {params.stage_labels.get(stage, stage)}; price it by hand")
                    continue
                lines.append(FeeLine(service, stage, "per_hectare", round(ha, 2), "ha",
                                     round(base * float(band_factor.value) * mod * float(sf), 2),
                                     round_sig(base * float(band_factor.value) * mod * float(sf) * fx, 3),
                                     [first, above, band_factor, Factor(f"masterplan.stage_factor.{stage}", float(sf),
                                                                        "assumption")]))
        else:
            warnings.append(f"No pricing method for the service {service}")

    if not lines:
        raise PricingError("nothing could be priced for these services and stages")
    total_local = sum(ln.amount_local for ln in lines)
    total_usd = sum(ln.amount_usd for ln in lines)
    reimb = reimbursables(p, params, archive or [], fx)
    return FeeEstimate(
        engine_version=FEE_ENGINE_VERSION, inputs=p, currency=p.currency, fx_per_usd=fx, lines=lines,
        fee_total_local=total_local, fee_total_usd=round(total_usd, 2), reimbursables=reimb,
        reimbursables_total_local=sum(r.amount_local for r in reimb),
        payment_schedule=payment_schedule(p, params, lines, total_local),
        programme=[{"stage": s, "label": params.stage_labels.get(s, s),
                    "duration": f"{v:g} weeks" if isinstance(v, float) else str(v)}
                   for s in p.stages if (v := params.values.get(f"programme.weeks.{s}")) is not None],
        deliverables=deliverables(p, reimb),
        factors=factors,
        range=fee_range(total_usd, p, params, archive or []),
        warnings=warnings,
    )


def nice(x: float) -> float:
    """Unit rates as a person would write them: two significant figures."""
    return round_sig(x, 2)


def reimbursables(p: PricingInputs, params: Parameters, archive: list[dict], fx: float) -> list[Reimbursable]:
    same = [a for a in archive if a["typology"] == p.typology and a.get("renders")] or \
           [a for a in archive if a.get("renders")]
    if same:
        renders = int(statistics.median(a["renders"] for a in same))
        why = f"quantity is the median of {len(same)} similar past proposals"
    else:
        renders = int(params.num("render.default_qty"))
        why = "no similar past proposals, so the workspace default quantity"
    rate = nice(params.num(f"render.unit_price.{params.band(p.country)}_cost") * fx)
    out = [Reimbursable("renders", renders, rate, renders * rate, f"Marketing-quality still images; {why}")]
    studio_country = params.studio_country.get(p.studio or "")
    if studio_country and studio_country != p.country:
        long_haul = params.regions.get(studio_country) != params.regions.get(p.country)
        kind = "long_haul" if long_haul else "regional"
        trip_rate = nice(params.num(f"trip.{kind}", 3500) * fx)
        trips = len(p.stages) + 1
        out.append(Reimbursable("site_trip", trips, trip_rate, trips * trip_rate,
                                f"{kind.replace('_', ' ')} trip, 2 staff, flights, hotel, per diem; "
                                f"one per stage plus a kick-off"))
    return out


def payment_schedule(p: PricingInputs, params: Parameters, lines: list[FeeLine], total: float) -> list[Milestone]:
    by_stage = {s: sum(ln.amount_local for ln in lines if ln.stage == s) for s in p.stages}
    return schedule(by_stage, total, params.num("payment.advance_pct", 10), params.stage_labels)


def schedule(by_stage: dict[str, float], total: float, advance: float, labels: dict[str, str]) -> list[Milestone]:
    """An advance, then one milestone per stage in proportion to its fee. Percentages are rounded to 0.1 and
    the last stage takes the remainder, so they always sum to exactly 100."""
    if total <= 0:
        return []
    out = [Milestone(1, "Advance on signing", None, advance, round(total * advance / 100))]
    left = 100 - advance
    stages = [s for s, v in by_stage.items() if v]
    for i, stage in enumerate(stages):
        pct = round(left - sum(m.pct for m in out[1:]), 1) if i == len(stages) - 1 else \
            round(by_stage[stage] / total * left, 1)
        out.append(Milestone(i + 2, f"On approval of {labels.get(stage, stage)}", stage, pct,
                             round(total * pct / 100)))
    return out


def deliverables(p: PricingInputs, reimb: list[Reimbursable]) -> list[dict]:
    renders = next((r.qty for r in reimb if r.type == "renders"), 0)
    out = [{"item": "Design report and drawings per stage", "qty": len(p.stages)},
           {"item": "Rendered images", "qty": renders},
           {"item": "Client presentations", "qty": len(p.stages) + 1}]
    if "masterplan" in p.services:
        out.append({"item": "Masterplan framework report", "qty": 1})
    return out


# ---------------------------------------------------------------- settings readiness

def readiness(conn: Connection) -> list[dict]:
    """What this workspace still needs before every request in its markets can be priced.
    severity "blocks": pricing fails; "limits": it prices, with a gap the estimator fills by hand."""
    from bidwright.config import REPO_ROOT
    starter = json.loads((REPO_ROOT / "data" / "reference" / "v0.json").read_text())
    try:
        params = Parameters(conn)
    except PricingError as e:
        return [dict(area="fee_method", severity="blocks", message=str(e))]
    out = []
    missing = [a["key"] for a in starter["fee_method"] if a["key"] not in params.values]
    if missing:
        out.append(dict(area="fee_method", severity="blocks",
                        message=f"{len(missing)} fee method values are missing: {', '.join(missing[:6])}"
                                f"{' ...' if len(missing) > 6 else ''}"))
    locations = conn.execute(text("SELECT country, city FROM ref.location ORDER BY country, city")).all()
    no_market = sorted({c for c, _ in locations if c not in params.markets})
    if no_market:
        out.append(dict(area="markets", severity="blocks",
                        message=f"no market settings (band, fee factor, escalation) for {', '.join(no_market)}"))
    no_cost = [f"{city}, {c}" for c, city in locations
               if (c, city, "average") not in params.costs and (c, "", "average") not in params.costs]
    if no_cost:
        out.append(dict(area="construction_costs", severity="blocks",
                        message=f"no construction cost for {'; '.join(no_cost)}; the estimator must enter one"))
    no_fx = sorted({params.national_currency[c] for c in params.markets
                    if params.national_currency.get(c) not in params.fx})
    if no_fx:
        out.append(dict(area="fx", severity="limits", message=f"no FX rate for {', '.join(no_fx)}; priced in USD"))
    if not params.studio_country:
        out.append(dict(area="studios", severity="limits",
                        message="no studios: site trips are never added to reimbursables"))
    synthetic = sum(1 for r in params.costs.values() if r.basis == "synthetic")
    if synthetic:
        out.append(dict(area="construction_costs", severity="limits",
                        message=f"{synthetic} construction cost figures are synthetic starter values; "
                                "replace them with the practice's own"))
    return out


# ---------------------------------------------------------------- the archive

def load_archive(conn: Connection, dataset_version: str, before: date | None = None) -> list[dict]:
    """Past proposals as pricing inputs, with what was actually quoted."""
    rows = conn.execute(text("""
        SELECT p.proposal_id, p.reference, p.title, p.typology, p.sub_typology, p.gfa_m2, p.site_area_m2,
               p.fitout_area_m2, p.landscape_area_m2, p.client_status, p.complexity, p.currency, p.submission_date,
               p.status, p.loss_reason, p.studio_code, l.country, l.city, l.tier,
               array(SELECT service FROM archive.proposal_service s WHERE s.proposal_id = p.proposal_id) AS services,
               array(SELECT stage FROM archive.proposal_stage s WHERE s.proposal_id = p.proposal_id) AS stages,
               (SELECT sum(amount_usd) FROM archive.fee_line f WHERE f.proposal_id = p.proposal_id) AS fees_usd,
               (SELECT qty FROM archive.reimbursable r WHERE r.proposal_id = p.proposal_id AND r.type = 'renders')
                 AS renders
        FROM archive.proposal p JOIN ref.location l USING (location_id)
        WHERE p.dataset_version = :v AND p.data_origin <> 'rfp_new'
          AND (CAST(:before AS date) IS NULL OR p.submission_date < CAST(:before AS date))
        ORDER BY p.proposal_id"""), dict(v=dataset_version, before=before)).all()
    return [dict(r._mapping) for r in rows]


def archive_inputs(a: dict, params: Parameters) -> PricingInputs:
    return inputs_from_record({
        "services": a["services"], "stage_package": a["stages"], "country": a["country"], "city": a["city"],
        "location_tier": a["tier"], "typology": a["typology"], "cost_type": a["sub_typology"],
        "gfa_m2": a["gfa_m2"], "site_area_m2": a["site_area_m2"], "fitout_area_m2": a["fitout_area_m2"],
        "landscape_area_m2": a["landscape_area_m2"], "client_status": a["client_status"],
        "complexity": a["complexity"], "billing_currency": a["currency"], "routed_studio": a["studio_code"],
    }, params, a["submission_date"].year)


def model_total_usd(a: dict, params: Parameters) -> float | None:
    """What this engine would have quoted for a past proposal, in USD, without its range."""
    try:
        p = archive_inputs(a, params)
        return estimate_lines_only(p, params)
    except (MissingInputs, PricingError):
        return None


def estimate_lines_only(p: PricingInputs, params: Parameters) -> float:
    return sum(ln.amount_usd for ln in estimate(p, params, archive=[]).lines)


def fee_range(total_usd: float, p: PricingInputs, params: Parameters, archive: list[dict]) -> dict | None:
    """How the firm's own bids sat against this model: the spread of won bids, and where lost bids began."""
    ratios = []
    for a in archive:
        if not a["fees_usd"] or a["status"] not in ("won", "lost"):
            continue
        model = model_total_usd(a, params)
        if model:
            ratios.append((a["status"], float(a["fees_usd"]) / model, a["reference"]))
    won = sorted(r for s, r, _ in ratios if s == "won")
    lost = sorted(r for s, r, _ in ratios if s == "lost")
    if len(won) < 2:
        return None
    fx = params.fx.get(p.currency, 1.0)
    return {
        "low_local": round_sig(total_usd * won[0] * fx, 3),
        "high_local": round_sig(total_usd * won[-1] * fx, 3),
        "won_ratios": [round(r, 3) for r in won],
        "lost_ratios": [round(r, 3) for r in lost],
        "basis": f"{len(won)} won bids priced between {won[0]:.0%} and {won[-1]:.0%} of this model"
                 + (f"; lost bids started at {lost[0]:.0%}" if lost else ""),
        "cases": [ref for _, _, ref in ratios],
    }


# ---------------------------------------------------------------- backtest

def backtest(conn: Connection, dataset_version: str) -> list[dict]:
    params = Parameters(conn)
    out = []
    for a in load_archive(conn, dataset_version):
        model = model_total_usd(a, params)
        actual = float(a["fees_usd"] or 0)
        out.append({"proposal_id": a["proposal_id"], "status": a["status"], "actual_usd": round(actual),
                    "model_usd": round(model) if model else None,
                    "ratio": round(actual / model, 3) if model else None})
    return out


def main() -> None:
    from bidwright.config import settings
    from bidwright.db import engine, workspace

    parser = argparse.ArgumentParser(description="Backtest the fee engine on a workspace's archive")
    parser.add_argument("--dataset", default=settings.dataset_version)
    args = parser.parse_args()
    with engine.connect() as conn:
        org_id = conn.execute(text("SELECT org_id FROM tenancy.organization WHERE is_demo")).scalar()
    with workspace(org_id), engine.connect() as conn:
        results = backtest(conn, args.dataset)
    print(f"{'case':6} {'status':10} {'actual USD':>12} {'model USD':>12} {'actual/model':>13}")
    for r in results:
        print(f"{r['proposal_id']:6} {r['status']:10} {r['actual_usd']:>12,} "
              f"{(r['model_usd'] or 0):>12,} {r['ratio'] if r['ratio'] is not None else '-':>13}")
    for status in ("won", "lost", "submitted", "withdrawn"):
        rs = [r["ratio"] for r in results if r["status"] == status and r["ratio"]]
        if rs:
            print(f"{status:10} n={len(rs):2}  median {statistics.median(rs):.3f}  range {min(rs):.3f}-{max(rs):.3f}")


if __name__ == "__main__":
    main()
