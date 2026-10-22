from sqlalchemy import inspect, text
from services.db import Base, database, Conversation
from services.migrate import migrate

def test_fresh_migration(tmp_path):
    url = f"sqlite:///{tmp_path/'fresh.db'}"
    assert migrate(url) == "fresh"
    engine, _ = database(url)
    with engine.connect() as connection:
        assert connection.execute(text("select version_num from alembic_version")).scalar() == "0007_s25_evidence"
        assert set(Base.metadata.tables).issubset(set(inspect(engine).get_table_names()))
    engine.dispose()

def test_existing_database_stamped_without_losing_data(tmp_path):
    url = f"sqlite:///{tmp_path/'legacy.db'}"
    engine, Session = database(url)
    Base.metadata.create_all(engine)
    with Session.begin() as db:
        db.add(Conversation(id="persisted-conversation", owner_id="test"))
    engine.dispose()
    assert migrate(url) == "validated_existing"
    engine, Session = database(url)
    with Session() as db:
        assert db.get(Conversation, "persisted-conversation") is not None
    engine.dispose()

def test_unversioned_legacy_database_upgrades_without_data_loss(tmp_path):
    import os
    from alembic import command
    from alembic.config import Config
    url=f"sqlite:///{tmp_path/'preexisting.db'}"
    config=Config("alembic.ini")
    config.set_main_option("sqlalchemy.url",url)
    prior=os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"]=url
    try:
        command.upgrade(config, "0001_s0_s1")
    finally:
        if prior is None:
            os.environ.pop("DATABASE_URL",None)
        else:
            os.environ["DATABASE_URL"]=prior
    engine,_=database(url)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO conversations(id, owner_id, created_at) VALUES ('legacy-cid','legacy-owner', CURRENT_TIMESTAMP)"))
        conn.execute(text("DROP TABLE alembic_version"))
    engine.dispose()
    assert migrate(url) == "validated_legacy"
    engine,Session=database(url)
    with Session() as db:
        assert db.get(Conversation,"legacy-cid") is not None
    assert "conversation_tasks" in inspect(engine).get_table_names()
    engine.dispose()
