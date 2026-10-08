import pytest
from sqlalchemy import text

from bidwright.db import engine


@pytest.fixture(scope="session")
def demo_org():
    with engine.connect() as conn:
        org_id = conn.execute(text("SELECT org_id FROM tenancy.organization WHERE is_demo")).scalar()
    if org_id is None:
        pytest.skip("no demo workspace; run make seed")
    return org_id
