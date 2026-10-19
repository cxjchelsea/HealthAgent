"""Explicit migration entry point. Does not drop or overwrite user data.

Usage: python -m services.migrate
For legacy create_all databases, validate exact S0/S1 columns before stamping.
"""
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from agent.config import Settings
from services.db import Base, engine_for

def migrate(database_url: str | None = None) -> str:
    url = database_url or Settings().database_url
    engine = engine_for(url)
    try:
        inspector = inspect(engine)
        existing = set(inspector.get_table_names()) - {"alembic_version"}
        expected = set(Base.metadata.tables)
        if existing and "alembic_version" not in inspector.get_table_names():
            if existing != expected:
                raise RuntimeError(f"Existing unversioned tables differ; manual migration required: {sorted(existing ^ expected)}")
            for table in Base.metadata.sorted_tables:
                actual = {c["name"] for c in inspector.get_columns(table.name)}
                required = {c.name for c in table.columns}
                if actual != required:
                    raise RuntimeError(f"Unversioned schema mismatch in {table.name}; backup and review required")
            mode = "validated_existing"
        elif existing:
            mode = "versioned"
        else:
            mode = "fresh"
    finally:
        engine.dispose()
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    # The env.py reads Settings(), so replace the environment just for this call.
    import os
    original = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url
    try:
        if mode == "validated_existing":
            command.stamp(config, "head")
        else:
            command.upgrade(config, "head")
    finally:
        if original is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = original
    return mode

if __name__ == "__main__":
    print("migration_mode:", migrate())
