from sqlalchemy import inspect, text
from services.db import Base, database, Conversation
from services.migrate import migrate

def test_fresh_migration(tmp_path):
    url = f"sqlite:///{tmp_path/'fresh.db'}"
    assert migrate(url) == "fresh"
    engine, _ = database(url)
    with engine.connect() as connection:
        assert connection.execute(text("select version_num from alembic_version")).scalar() == "0001_s0_s1"
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
