"""Database layer for the Rednote workspace module.

Postgres is the target store (see compose.yaml). SQLite is supported for tests
and local smoke runs. When DATABASE_URL is unset the workspace APIs report
CAPABILITY_DISABLED instead of pretending to persist anything.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

_engine = None
_session_factory: sessionmaker | None = None


class Base(DeclarativeBase):
    pass


def database_url() -> str:
    return (os.getenv("DATABASE_URL") or "").strip()


def default_sqlite_path() -> str:
    """File-backed fallback for single-instance demos (never in-memory)."""
    configured_path = (os.getenv("AGENT_DB_PATH") or "").strip()
    if configured_path:
        return configured_path
    if os.path.isdir("/app/data"):
        return "/app/data/agent_v2.db"
    return os.path.join(os.getcwd(), "data", "agent_v2.db")


def ensure_configured() -> bool:
    """Use DATABASE_URL, or create a file-backed SQLite DB for a demo instance.

    The fallback keeps v2 conversations across requests and process restarts on
    one machine. It is not a substitute for the Postgres deployment and never
    falls back to an in-process dictionary.
    """
    if configured():
        return True
    path = default_sqlite_path()
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        os.environ["DATABASE_URL"] = "sqlite:///" + path
        init_db()
    except (OSError, RuntimeError):
        return False
    return True


def configured() -> bool:
    return bool(database_url())


def _apply_sqlite_pragmas(engine) -> None:
    """WAL plus a busy timeout, so concurrent runs wait instead of failing.

    SQLite still allows only one writer at a time, so this reduces collisions rather than
    removing the limit; a real deployment uses the configured Postgres target.
    """
    from sqlalchemy import event

    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        try:
            try:
                cursor.execute("PRAGMA journal_mode=WAL")
            except Exception:  # noqa: BLE001 - in-memory databases cannot use WAL
                pass
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA busy_timeout=5000")
        finally:
            cursor.close()


def engine():
    global _engine, _session_factory
    url = database_url()
    if not url:
        raise RuntimeError("DATABASE_URL 未配置")
    if _engine is None or str(_engine.url) != url:
        # `timeout` is pysqlite's busy timeout: without it two concurrent runs collide on
        # SQLite's single writer and one of them fails immediately with "database is locked"
        # instead of waiting its turn.
        connect_args = ({"check_same_thread": False, "timeout": 5.0}
                        if url.startswith("sqlite") else {})
        _engine = create_engine(url, pool_pre_ping=True, future=True, connect_args=connect_args)
        if url.startswith("sqlite"):
            _apply_sqlite_pragmas(_engine)
        _session_factory = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False, future=True)
    return _engine


def session_factory() -> sessionmaker:
    engine()
    assert _session_factory is not None
    return _session_factory


def init_db() -> None:
    from . import models  # noqa: F401  (register tables)
    Base.metadata.create_all(engine())


def reset_for_tests(url: str) -> None:
    """Point the module at a fresh database; used by tests."""
    global _engine, _session_factory
    _engine = None
    _session_factory = None
    os.environ["DATABASE_URL"] = url
    init_db()


@contextmanager
def session_scope() -> Iterator[Session]:
    db = session_factory()()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_db() -> Iterator[Session]:
    db = session_factory()()
    try:
        yield db
    finally:
        db.close()


if __name__ == "__main__":  # pragma: no cover - CLI helper
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "init":
        init_db()
        print("database initialised:", database_url())
    else:
        print("usage: python -m app.db init")
