from sqlalchemy import inspect, text
from services.db import Base, database, Conversation
from services.migrate import migrate

def test_fresh_migration(tmp_path):
    url = f"sqlite:///{tmp_path/'fresh.db'}"
    assert migrate(url) == "fresh"
    engine, _ = database(url)
    with engine.connect() as connection:
        assert connection.execute(text("select version_num from alembic_version")).scalar() == "0021_turn_user_message"
        inspector = inspect(engine)
        assert "user_message_id" in {col["name"] for col in inspector.get_columns("turns")}
        assert set(Base.metadata.tables).issubset(set(inspector.get_table_names()))
        assert "tool_version" in {c["name"] for c in inspector.get_columns("agent_attempts")}
        assert "tool_version" in {c["name"] for c in inspector.get_columns("plan_tool_dispatches")}
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


def test_0020_to_0021_preserves_existing_turns_as_unbound(tmp_path, monkeypatch):
    """Historical messages must not be inferred by role or chronology."""
    from alembic import command
    from alembic.config import Config
    from services.db import Turn
    from sqlalchemy.exc import IntegrityError

    url = f"sqlite:///{tmp_path / 'from-0020.db'}"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)
    monkeypatch.setenv("DATABASE_URL", url)
    command.upgrade(cfg, "0020_dependency_fence")
    engine, Session = database(url)
    with engine.begin() as db:
        db.execute(text(
            "INSERT INTO conversations(id, owner_id, created_at) "
            "VALUES ('legacy-conv', 'owner', CURRENT_TIMESTAMP)"))
        db.execute(text(
            "INSERT INTO messages(id, conversation_id, role, content, status, created_at) "
            "VALUES ('legacy-msg', 'legacy-conv', 'user', 'old request', 'completed', CURRENT_TIMESTAMP)"))
        for tid in ("legacy-turn-1", "legacy-turn-2"):
            db.execute(text(
                "INSERT INTO turns(id, conversation_id, request_id, state, "
                "input_chars, output_chars, duration_ms, decision_steps) "
                "VALUES (:id, 'legacy-conv', :request_id, 'completed', "
                "11, 0, 0, 0)"),
                {"id": tid, "request_id": tid})
    engine.dispose()
    assert migrate(url) == "versioned"
    assert migrate(url) == "versioned"
    engine, Session = database(url)
    inspector = inspect(engine)
    cols = {c["name"]: c for c in inspector.get_columns("turns")}
    assert cols["user_message_id"]["nullable"] is True
    assert "uq_turn_user_message_id" in {
        unique["name"] for unique in inspector.get_unique_constraints("turns")}
    assert "fk_turn_user_message" in {
        fk["name"] for fk in inspector.get_foreign_keys("turns")}
    with Session() as db:
        assert db.get(Turn, "legacy-turn-1").user_message_id is None
        assert db.get(Turn, "legacy-turn-2").user_message_id is None
        assert db.get(Turn, "legacy-turn-1").input_chars == 11
    with engine.begin() as db:
        db.execute(text(
            "INSERT INTO messages(id, conversation_id, role, content, status, created_at) "
            "VALUES ('new-msg', 'legacy-conv', 'user', 'current request', 'completed', CURRENT_TIMESTAMP)"))
        db.execute(text(
            "UPDATE turns SET user_message_id='new-msg' WHERE id='legacy-turn-1'"))
    try:
        with engine.begin() as db:
            db.execute(text(
                "UPDATE turns SET user_message_id='new-msg' WHERE id='legacy-turn-2'"))
        raise AssertionError("database allowed two Turns to reuse one User Message")
    except IntegrityError:
        pass
    with Session() as db:
        assert db.get(Turn, "legacy-turn-2").user_message_id is None
    engine.dispose()
