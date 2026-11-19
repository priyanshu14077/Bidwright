"""Engine rules against the loaded reference data. Needs the database up and the loader run."""

import pytest

from bidwright.db import engine, workspace
from bidwright.engine import Candidate, Reference, consolidate, normalize
from bidwright.schema import fields


@pytest.fixture(scope="module")
def ref(demo_org):
    with workspace(demo_org), engine.connect() as conn:
        return Reference(conn)


def cand(i, field, value_text, number=None, unit="none", normalized_text=None, doc_class="main_rfp"):
    return Candidate(i, field, {"value_text": value_text, "number": number, "unit": unit,
                                "normalized_text": normalized_text, "quote": value_text}, i, doc_class, 0.9)


@pytest.mark.parametrize("written,country,code", [
    ("Scheme Design (Anteproyecto)", "ES", "schematic_design"),
    ("Proyecto Básico", "ES", "design_development"),
    ("Basic Design / Design Development", "VN", "design_development"),
    ("Preliminary Design (DD)", "CN", "design_development"),
    ("RIBA Stage 3 - Spatial Coordination", "SA", "schematic_design"),
    ("GFC Drawing Review", "AE", "gfc_review"),
    ("Construction Stage Design Review", "SG", "construction_review"),
    ("Feasibility Study", "CN", "feasibility"),
    ("Preliminary Drawings", "IN", "schematic_design"),
])
def test_stage_names_map_to_stage_codes(ref, written, country, code):
    c = cand(1, "stage_package", written)
    normalize(fields()["stage_package"], c, ref, country)
    assert c.normalized == code


def test_unknown_stage_is_rejected_not_guessed(ref):
    c = cand(1, "stage_package", "Contract Documentation", normalized_text="unmapped")
    flags = normalize(fields()["stage_package"], c, ref, "AU")
    assert c.status == "rejected" and flags[0].rule == "unmapped_term"


def test_indian_sqft_converts_to_m2(ref):
    c = cand(1, "gfa_m2", "11,84,030 sq ft", number=1184030, unit="sqft")
    normalize(fields()["gfa_m2"], c, ref, "IN")
    assert c.normalized == pytest.approx(110_000, rel=0.001)


def test_rounded_hectares_agree_with_exact_m2(ref):
    f = fields()["site_area_m2"]
    a = cand(1, "site_area_m2", "6.2 ha", number=6.2, unit="ha")
    b = cand(2, "site_area_m2", "62,000", number=62000, unit="m2", doc_class="area_schedule")
    for c in (a, b):
        normalize(f, c, ref, "AE")
    _, conflict = consolidate(f, [a, b], {})
    assert conflict is None


def test_brief_vs_schedule_gfa_is_a_conflict_and_schedule_leads(ref):
    f = fields()["gfa_m2"]
    brief = cand(1, "gfa_m2", "approximately 42,180 m²", number=42180, unit="m2", doc_class="main_rfp")
    schedule = cand(2, "gfa_m2", "38,000", number=38000, unit="m2", doc_class="area_schedule")
    for c in (brief, schedule):
        normalize(f, c, ref, "AE")
    _, conflict = consolidate(f, [brief, schedule], {1: "RFP_Main.pdf", 2: "Area_Schedule.pdf"})
    assert conflict is not None and conflict["ids"][0] == 2


def test_number_not_in_quote_is_flagged(ref):
    c = cand(1, "gfa_m2", "38,000 m²", number=83000, unit="m2")
    flags = normalize(fields()["gfa_m2"], c, ref, "AE")
    assert any(fl.rule == "number_not_in_source" for fl in flags) and c.normalized == 38000  # the written figure


def test_an_area_the_model_already_converted_is_read_from_the_words(ref):
    """'4-acre' with number 16187 and unit acre would be 65 million m²; the written 4 acres is what counts."""
    c = cand(1, "site_area_m2", "4-acre", number=16187, unit="acre")
    flags = normalize(fields()["site_area_m2"], c, ref, "IN")
    assert c.normalized == pytest.approx(16187, rel=0.001) and [f.rule for f in flags] == ["number_not_in_source"]
    assert "read 4 from '4-acre'" in c.notes[0]


@pytest.mark.parametrize("said,sent,due", [
    ("Thursday next week", "2026-11-19", "2026-11-26"),   # sent on a Thursday
    ("by next Thu", "2026-11-16", "2026-11-26"),
    ("by Friday", "2026-11-18", "2026-11-20"),
    ("Friday next week", "2026-11-18", "2026-11-27"),
    ("end of month", "2026-11-19", "2026-11-30"),
    ("end of next week", "2026-11-19", "2026-11-27"),
    ("first thing Monday 23rd", "2026-11-19", None),      # an explicit date is left to what is written
])
def test_relative_deadlines_are_worked_out_from_the_day_they_were_sent(said, sent, due):
    from datetime import date
    from bidwright.engine import relative_date
    got = relative_date(said, date.fromisoformat(sent))
    assert (got.isoformat() if got else None) == due


def test_the_same_deadline_with_and_without_a_time_zone_is_not_a_conflict():
    from bidwright.engine import same
    f = fields()["submission_deadline"]
    email = cand(1, "submission_deadline", "Monday 14 December 2026, 12:00 Riyadh time")
    brief = cand(2, "submission_deadline", "12:00 on Monday 14 December 2026")
    email.normalized, brief.normalized = "2026-12-14T12:00:00+03:00", "2026-12-14T12:00:00+00:00"
    assert same(f, email, brief)
    later = cand(3, "submission_deadline", "Tuesday 15 December 2026, 12:00")
    later.normalized = "2026-12-15T12:00:00+03:00"
    assert not same(f, email, later)
    _, conflict = consolidate(f, [email, brief], {1: "RFP.eml", 2: "Brief.pdf"})
    assert conflict is None
