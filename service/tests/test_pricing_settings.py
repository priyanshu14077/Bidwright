"""Pricing settings: the generated cost book, seeding, lookups and edits. Needs the database up and the seed run."""

import importlib.util
import json

import pytest
from sqlalchemy import text

from bidwright.config import REPO_ROOT
from bidwright.db import engine, workspace
from bidwright.fees import COST_TYPES, Parameters, PricingError, estimate, inputs_from_record

REFERENCE = REPO_ROOT / "data" / "reference"


def test_cost_book_file_is_what_the_generator_writes():
    spec = importlib.util.spec_from_file_location("generate_cost_book", REFERENCE / "generate_cost_book.py")
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    assert gen.build() == json.loads((REFERENCE / "cost-book-v0.json").read_text())


def test_every_starter_city_has_its_own_figures(demo_org):
    with workspace(demo_org), engine.connect() as conn:
        params = Parameters(conn)
    locations = json.loads((REFERENCE / "v0.json").read_text())["locations"]
    for country, city, _, _ in locations:
        for cost_type in COST_TYPES:
            usd, year, how = params.cost_rate(country, city, cost_type)
            assert usd > 0 and year == 2025 and "country-wide" not in how, (city, cost_type)


def test_unknown_city_falls_back_to_the_country_and_says_so(demo_org):
    with workspace(demo_org), engine.connect() as conn:
        params = Parameters(conn)
    usd, _, how = params.cost_rate("AE", "Fujairah", "office")
    assert usd == params.cost_rate("AE", "", "office")[0]
    assert "country-wide (no Fujairah figures)" in how


def test_city_aliases_find_the_city(demo_org):
    with workspace(demo_org), engine.connect() as conn:
        params = Parameters(conn)
    assert params.cost_rate("AE", "RAK", "office") == params.cost_rate("AE", "Ras Al Khaimah", "office")


def test_new_workspace_can_price_without_an_archive(stranger):
    client, org = stranger
    with workspace(org), engine.connect() as conn:
        params = Parameters(conn)
    record = {"services": ["architecture", "landscape"], "stage_package": ["concept", "schematic_design"],
              "country": "AU", "city": "Sydney", "location_tier": "prime", "typology": "office",
              "gfa_m2": 20000, "landscape_area_m2": 4000}
    est = estimate(inputs_from_record(record, params, 2026), params, [])
    assert est.fee_total_local > 0 and est.range is None
    assert est.reimbursables[0].qty == 8 and "default" in est.reimbursables[0].note
    ready = client.get("/api/settings/pricing").json()["readiness"]
    assert not [r for r in ready if r["severity"] == "blocks"]
    assert any(r["area"] == "studios" for r in ready)


def test_estimator_figure_replaces_the_book_and_is_logged(stranger):
    client, org = stranger
    body = {"country": "AU", "city": "Sydney", "cost_type": "office", "usd_m2": 4000, "price_year": 2026,
            "reason": "QS quote"}
    r = client.put("/api/settings/pricing/construction-costs", json=body)
    assert r.status_code == 200, r.text
    with workspace(org), engine.connect() as conn:
        params = Parameters(conn)
        change = conn.execute(text("SELECT key, old, new, reason FROM ref.setting_change")).one()
    assert params.cost_rate("AU", "Sydney", "office")[:2] == (4000, 2026)
    assert change.key == "AU/Sydney/office" and change.old["basis"] == "synthetic" and change.reason == "QS quote"

    assert client.put("/api/settings/pricing/markets/AU", json={"escalation_pct": 6}).status_code == 200
    assert client.put("/api/settings/pricing/fee-method/scale_exponent", json={"value": "x"}).status_code == 422
    assert client.put("/api/settings/pricing/fee-method/no.such.key", json={"value": 1}).status_code == 404
    r = client.put("/api/settings/pricing/construction-costs", json=dict(body, cost_type="warehouse"))
    assert r.status_code == 422


def test_missing_market_blocks_pricing(stranger):
    _, org = stranger
    with workspace(org), engine.begin() as conn:
        conn.execute(text("DELETE FROM ref.market WHERE country = 'AU'"))
        params = Parameters(conn)
    record = {"services": ["architecture"], "stage_package": ["concept"], "country": "AU", "city": "Sydney",
              "location_tier": "prime", "typology": "office", "gfa_m2": 20000}
    with pytest.raises(PricingError, match="no market settings for AU"):
        estimate(inputs_from_record(record, params, 2026), params, [])
