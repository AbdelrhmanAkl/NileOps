from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from src.core.config import get_settings


settings = get_settings()
database_url = make_url(settings.database_url)

engine_options: dict[str, object] = {
    "pool_pre_ping": True,
}

if database_url.get_backend_name() == "sqlite":
    if database_url.database not in (None, "", ":memory:"):
        database_path = database_url.database

        if not database_url.database.startswith("file:"):
            from pathlib import Path

            path = Path(database_path)
            if not path.is_absolute():
                path = Path(__file__).resolve().parents[2] / path

            path.parent.mkdir(parents=True, exist_ok=True)
            database_url = database_url.set(database=str(path))

    engine_options["connect_args"] = {"check_same_thread": False}

def enable_sqlite_foreign_keys(target: Engine) -> Engine:
    """Make SQLite enforce FOREIGN KEY constraints (it ignores them unless asked, per connection)."""

    @event.listens_for(target, "connect")
    def _foreign_keys_on(dbapi_connection, _record) -> None:  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return target


engine = create_engine(database_url, **engine_options)
if database_url.get_backend_name() == "sqlite":
    enable_sqlite_foreign_keys(engine)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    """Base class for SQLAlchemy ORM models."""


def get_db() -> Generator[Session, None, None]:
    """Provide a database session and always close it afterward."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
