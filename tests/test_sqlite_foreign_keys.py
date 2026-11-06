"""SQLite ignores FOREIGN KEY constraints unless the pragma is enabled on every connection."""

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import StaticPool

from src.db.session import enable_sqlite_foreign_keys


def make_engine(enforce: bool):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    if enforce:
        enable_sqlite_foreign_keys(engine)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE parent (id INTEGER PRIMARY KEY)"))
        conn.execute(text("CREATE TABLE child (id INTEGER PRIMARY KEY, parent_id INTEGER REFERENCES parent(id))"))
    return engine


def test_without_the_pragma_orphans_are_silently_accepted():
    engine = make_engine(enforce=False)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO child (id, parent_id) VALUES (1, 999)"))


def test_with_the_pragma_orphans_are_rejected():
    engine = make_engine(enforce=True)
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO child (id, parent_id) VALUES (1, 999)"))


def test_application_engine_enforces_foreign_keys():
    from src.db.session import engine

    if engine.dialect.name != "sqlite":
        pytest.skip("SQLite only")
    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1
