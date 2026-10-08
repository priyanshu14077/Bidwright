"""Database engines and the workspace boundary.

Every transaction starts by setting app.org_id from the current workspace, and
row-level security limits it to that workspace's rows. Outside a workspace the
setting is empty: tenant tables read as empty and inserts into them fail.

    with workspace(org_id):
        with engine.begin() as conn: ...

Context variables do not cross into new threads by themselves; submit work to a
pool with contextvars.copy_context().run so it keeps the workspace.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from uuid import UUID

from sqlalchemy import Connection, Engine, create_engine, event, text

from bidwright.config import settings

current_org: ContextVar[str | None] = ContextVar("current_org", default=None)


def _scoped(url: str) -> Engine:
    e = create_engine(url, pool_pre_ping=True)

    @event.listens_for(e, "begin")
    def set_workspace(conn):
        conn.exec_driver_sql("SELECT set_config('app.org_id', %s, true)", (current_org.get() or "",))

    return e


# The API and everything it calls. Not the table owner, cannot change the shared vocabulary.
engine = _scoped(settings.app_database_url)
# Seed and administration: owns the tables, and still bound by row-level security on workspace tables.
owner_engine = _scoped(settings.database_url)


@contextmanager
def workspace(org_id: UUID | str) -> Iterator[None]:
    token = current_org.set(str(org_id))
    try:
        yield
    finally:
        current_org.reset(token)


def enter(conn: Connection, org_id: UUID | str) -> None:
    """Move an open transaction into a workspace it just created."""
    conn.execute(text("SELECT set_config('app.org_id', :o, true)"), dict(o=str(org_id)))


def in_workspace(org_id: UUID | str, fn, *args, **kwargs):
    """Run fn inside a workspace; for background tasks."""
    with workspace(org_id):
        return fn(*args, **kwargs)
