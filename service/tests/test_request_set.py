"""The v1-requests gold set: reproducible, complete, and internally consistent. No database or model calls."""

import importlib.util
import json
from collections import Counter
from datetime import date, timedelta

import pytest

from bidwright.config import REPO_ROOT
from bidwright.intake_eval import FIELDS, same_value, score, status_ok, summarise

SET = REPO_ROOT / "data" / "gold" / "v1-requests"
TRUTH = json.loads((SET / "truth.json").read_text())
CASES, POLICY = TRUTH["cases"], TRUTH["policy"]
REFERENCE = json.loads((REPO_ROOT / "data" / "reference" / "v0.json").read_text())
ARCHIVE = json.loads((REPO_ROOT / "data" / "archive" / "v0-synthetic" / "archive.json").read_text())


def load_generator():
    spec = importlib.util.spec_from_file_location("generate_v1_requests", SET / "generate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_generator_reproduces_the_committed_files(tmp_path):
    gen = load_generator()
    for case_id, case in CASES.items():
        gen.render_case(case_id, case, tmp_path)
    made = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*") if p.is_file())
    committed = sorted(p.relative_to(SET / "requests") for p in (SET / "requests").rglob("*") if p.is_file())
    assert made == committed
    changed = [str(p) for p in made if (tmp_path / p).read_bytes() != (SET / "requests" / p).read_bytes()]
    assert not changed, f"regenerate with `make requests`: {changed}"


def test_every_request_has_files_and_a_status_for_every_field():
    for case_id, case in CASES.items():
        assert (SET / "requests" / case_id).is_dir(), case_id
        assert set(case["fields"]) == set(FIELDS), case_id
        for name, spec in case["fields"].items():
            assert spec[0] in ("found", "ambiguous", "missing", "referenced"), (case_id, name)
            assert (spec[1] is None) == (spec[0] == "missing") or spec[0] == "ambiguous", (case_id, name)
        assert set(case["expected"]["questions_cover"]) <= set(FIELDS), case_id


def test_the_mix_of_requests():
    kinds = Counter(c["kind"].split("_")[0] if c["kind"].startswith(("whatsapp", "email")) else c["kind"]
                    for c in CASES.values())
    assert kinds["whatsapp"] == 7 and kinds["email"] == 3 and kinds["full_pack"] == 3
    assert {c["demo"].split(";")[0] for c in CASES.values() if "demo" in c} == {"step 1", "step 3", "step 4"}
    assert {c["fields"]["country"][1] for c in CASES.values()} >= {"AE", "SG", "IN", "AU", "VN"}


# ---------------------------------------------------------------- the policy behind the expected outcomes

def market_currency(country: str) -> str | None:
    """The currency the practice has used in that market, else the national one."""
    codes = {name.lower(): r[0] for r in REFERENCE["countries"] for name in [r[0], r[1], *r[5]]}
    used = Counter(p["currency"] for p in ARCHIVE["proposals"] if codes.get(p["country"].lower()) == country)
    if used:
        return used.most_common(1)[0][0]
    return next((r[3] for r in REFERENCE["countries"] if r[0] == country), None)


def working_days_after(start: date, end: date) -> int:
    return sum(1 for i in range(1, (end - start).days + 1) if (start + timedelta(i)).weekday() < 5)


def derived_rules(case: dict) -> set[str]:
    f = {k: v[0] for k, v in case["fields"].items()}
    val = {k: v[1] for k, v in case["fields"].items()}
    served = {r[0] for r in REFERENCE["countries"]}
    services = {r[0] for r in REFERENCE["services"]}
    rules = set()
    if val["country"] not in served:
        rules.add("market_not_served")
    if f["services"] in ("found", "referenced") and not set(val["services"]) & services:
        rules.add("no_matching_service")
    if f["submission_deadline"] != "missing":
        received = date.fromisoformat(case["received_at"][:10])
        due = date.fromisoformat(val["submission_deadline"][:10])
        if working_days_after(received, due) < POLICY["deadline_min_working_days"]:
            rules.add("deadline_too_close")
    w = POLICY["weights"]
    if sum(w[k] for k, s in f.items() if s in ("found", "referenced")) / sum(w.values()) < POLICY["completeness_min"]:
        rules.add("low_completeness")
    if any(f[k] == "ambiguous" for k in POLICY["pricing_fields"]):
        rules.add("ambiguous_pricing_field")
    if not case["appointment_intent"]:
        rules.add("feasibility_only")
    if f["billing_currency"] != "found" and val["country"] in served \
            and market_currency(val["country"]) != POLICY["studio_currency"][case["studio"]]:
        rules.add("currency_unconfirmed")
    return rules


@pytest.mark.parametrize("case_id", sorted(CASES))
def test_expected_outcome_follows_the_policy(case_id):
    case = CASES[case_id]
    rules = derived_rules(case)
    assert rules == set(case["expected"]["rules"])
    severity = {POLICY["rules"][r] for r in rules}
    outcome = "no-go" if "no-go" in severity else "clarify" if "clarify" in severity else "go"
    assert outcome == case["expected"]["recommendation"]


def test_vietnam_is_quoted_in_the_currency_the_practice_uses_there():
    assert market_currency("VN") == "USD" and market_currency("AE") == "AED" and market_currency("AU") == "AUD"


# ---------------------------------------------------------------- scoring

def test_status_rules():
    assert status_ok("missing", "estimated") and status_ok("referenced", "estimated") and status_ok("found", "found")
    assert not status_ok("found", "estimated") and not status_ok("ambiguous", "found")


def test_value_rules():
    assert same_value("site_area_m2", 16187, 16190) and not same_value("gfa_m2", 41000, 36900)
    assert same_value("client_name", "Marina Crest", "Marina Crest Developments LLC")
    assert same_value("submission_deadline", "2026-12-03", "2026-12-03T14:00:00+04:00")
    assert same_value("services", ["architecture", "landscape"], ["landscape", "architecture"])


def test_score_and_summary():
    def v(n):
        return 100 if n.endswith("_m2") else ["architecture"] if n in ("services", "stage_package") else "x"

    case = {"case_id": "T1", "kind": "whatsapp_one_liner", "channel": "whatsapp",
            "fields": {n: {"status": "found", "value": v(n)} for n in FIELDS},
            "expected": {"recommendation": "clarify", "rules": ["low_completeness"], "questions_cover": ["gfa_m2"]}}
    case["fields"]["gfa_m2"] = {"status": "missing", "value": None}
    case["fields"]["billing_currency"] = {"status": "found", "value": "AED"}
    seen = {"fields": {n: {"status": "found", "value": v(n)} for n in FIELDS}, "recommendation": "go",
            "rules": [], "questions_cover": []}
    seen["fields"]["gfa_m2"] = {"status": "estimated", "value": 38000}
    seen["fields"]["billing_currency"] = {"status": "estimated", "value": "AED"}
    r = score(case, 1, seen)
    assert r["fields"]["gfa_m2"]["caught"] and r["fields"]["billing_currency"]["false_gap"]
    s = summarise([r])
    assert s["gaps_caught"] == "1/1" and s["false_gaps"] == "1/11" and s["recommendation_accuracy"] == 0.0
    assert s["question_coverage"] == 0.0 and s["recommendation_confusion"] == {"clarify": {"go": 1}}


def test_the_evaluation_ingests_its_own_copy_of_each_file():
    from bidwright.intake_eval import eval_copy
    for p in sorted((SET / "requests").rglob("*")):
        if p.is_file() and "followups" not in p.parts:
            assert eval_copy(p.name, p.read_bytes()) != p.read_bytes(), p.name
