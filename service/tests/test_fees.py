"""Fee engine against the demo workspace's archive. Needs the database up and the loader run."""

import json

import pytest
from sqlalchemy import text

from bidwright.config import settings
from bidwright.db import engine, workspace
from bidwright.fees import MissingInputs, Parameters, archive_inputs, estimate, inputs_from_record, load_archive


@pytest.fixture(scope="module")
def loaded(demo_org):
    with workspace(demo_org), engine.connect() as conn:
        params = Parameters(conn)
        archive = load_archive(conn, settings.dataset_version)
        traces = {}
        for pid, factor, value in conn.execute(text(
                "SELECT proposal_id, factor, value FROM archive.pricing_trace "
                "WHERE factor IN ('quote_variation', 'lost_bid_premium')")):
            traces.setdefault(pid, {})[factor] = float(value)
    return params, archive, traces


def test_model_reproduces_every_archive_fee(loaded):
    """The synthetic fees are this model times recorded noise and lost-bid premium; nothing else may differ."""
    params, archive, traces = loaded
    for a in archive:
        lines = estimate(archive_inputs(a, params), params).lines
        model = sum(ln.amount_usd for ln in lines)
        t = traces[a["proposal_id"]]
        expected = t["quote_variation"] * t["lost_bid_premium"]
        assert float(a["fees_usd"]) / model == pytest.approx(expected, abs=0.012), a["proposal_id"]


def test_lines_follow_the_archive_structure(loaded, demo_org):
    """Same services and stages priced, line for line, as each past proposal."""
    params, archive, _ = loaded
    with workspace(demo_org), engine.connect() as conn:
        quoted = {}
        for pid, service, stage in conn.execute(text("SELECT proposal_id, service, stage FROM archive.fee_line")):
            quoted.setdefault(pid, []).append((service, stage))
    for a in archive:
        est = estimate(archive_inputs(a, params), params)
        assert sorted((ln.service, ln.stage) for ln in est.lines) == sorted(quoted[a["proposal_id"]]), a["proposal_id"]


def test_payment_schedule_adds_up(loaded):
    params, archive, _ = loaded
    for a in archive:
        est = estimate(archive_inputs(a, params), params, archive)
        assert sum(m.pct for m in est.payment_schedule) == pytest.approx(100, abs=0.05), a["proposal_id"]
        assert est.payment_schedule[0].milestone == "Advance on signing"


def test_range_comes_from_won_bids_before_the_rfp(loaded):
    params, archive, _ = loaded
    p13 = next(a for a in archive if a["proposal_id"] == "P13")
    earlier = [a for a in archive if a["submission_date"] < p13["submission_date"]]
    est = estimate(archive_inputs(p13, params), params, earlier)
    assert est.range and est.range["low_local"] < est.fee_total_local < est.range["high_local"]
    assert all(ref != p13["reference"] for ref in est.range["cases"])


def test_missing_area_stops_pricing(loaded):
    params, _, _ = loaded
    record = {"services": ["architecture", "landscape"], "stage_package": ["concept"], "country": "AE",
              "location_tier": "prime", "typology": "office", "gfa_m2": 20000}
    with pytest.raises(MissingInputs) as err:
        inputs_from_record(record, params, 2026)
    assert err.value.fields == ["landscape_area_m2"]


def test_defaults_are_declared(loaded):
    params, _, _ = loaded
    record = {"services": ["architecture"], "stage_package": ["concept", "schematic_design"], "country": "AE",
              "city": "Dubai", "location_tier": "prime", "typology": "hospitality", "gfa_m2": 30000}
    p = inputs_from_record(record, params, 2026)
    assert p.cost_type == "hospitality_5star" and p.complexity == "medium" and p.currency == "AED"
    assert any("5-star" in note for note in p.assumed) and any("complexity" in note for note in p.assumed)
    est = estimate(p, params)
    assert json.dumps(est.to_dict(), default=str)
    assert [m.stage for m in est.payment_schedule[1:]] == ["concept", "schematic_design"]


def test_unpriced_stage_is_a_warning_not_a_guess(loaded):
    params, _, _ = loaded
    record = {"services": ["interior_design"], "stage_package": ["concept", "construction_review"],
              "country": "SG", "city": "Singapore", "location_tier": "prime", "typology": "office",
              "fitout_area_m2": 5000}
    est = estimate(inputs_from_record(record, params, 2026), params)
    assert [ln.stage for ln in est.lines] == ["concept"]
    assert any("Construction Review" in w for w in est.warnings)
