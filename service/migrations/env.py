"""Alembic environment.

Migrations here are written by hand. There is no target metadata, so
`alembic revision --autogenerate` produces nothing; that is deliberate.
The include_name filter keeps any future autogenerate away from schemas
this service does not own.
"""

from alembic import context
from sqlalchemy import engine_from_config, pool

from sog.config import settings

OWNED_SCHEMAS = {"ref", "archive", "lineage", "intake", "eval"}

config = context.config
config.set_main_option("sqlalchemy.url", settings.sog_database_url)


def include_name(name, type_, parent_names):
    if type_ == "schema":
        return name in OWNED_SCHEMAS
    return True


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=None,
            include_schemas=True,
            include_name=include_name,
            version_table_schema="public",
        )
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
