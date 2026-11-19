"""The pricing logic register lists every setting the fee engine reads, and traces a fee back to it."""
from sqlalchemy import text

from bidwright import fees, logic
from bidwright.db import engine, workspace


def demo_org():
    with engine.connect() as c:
        return c.execute(text("SELECT org_id FROM tenancy.organization WHERE slug = 'northbeam'")).scalar()


def test_every_fee_setting_is_in_the_register():
    with workspace(demo_org()), engine.connect() as conn:
        ids = {e["id"] for e in logic.entries(conn)}
        keys = set(conn.execute(text("SELECT key FROM ref.fee_assumption")).scalars())
        markets = {f"market.{c}" for c in conn.execute(text("SELECT country FROM ref.market")).scalars()}
        rules = {f"rule.{n}" for n in conn.execute(text("SELECT name FROM ref.qualification_rule")).scalars()}
    assert keys <= ids and markets <= ids and rules <= ids


def test_every_entry_has_a_section_value_and_source():
    with workspace(demo_org()), engine.connect() as conn:
        sections = {k for k, _, _ in logic.SECTIONS}
        for e in logic.entries(conn):
            assert e["section"] in sections and e["value"] and e["source"], e


def test_a_priced_fee_traces_to_register_entries():
    with workspace(demo_org()), engine.connect() as conn:
        ids = {e["id"] for e in logic.entries(conn)}
        env = conn.execute(text("""SELECT envelope_id FROM intake.assessment ORDER BY envelope_id DESC""")).scalars().all()
        for e in env:
            u = logic.used_by(conn, e)
            if u["priced"]:
                break
        else:
            return  # nothing priced in this workspace yet
    assert set(u["used"]) <= ids
    assert any(x.startswith("market.") for x in u["used"]) and any(x.startswith("stage_share.") or
                                                                   x.startswith("masterplan.") for x in u["used"])


def test_fmt_reads_like_the_settings():
    assert logic.fmt(0.15, "share of full-service fee") == "15% share of full-service fee"
    assert logic.fmt(0.95, "multiplier") == "× 0.95"
    assert logic.fmt(3.5, "% of construction cost") == "3.5% of construction cost"
    assert fees.FEE_ENGINE_VERSION
