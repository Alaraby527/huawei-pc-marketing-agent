"""Database layer for the Rednote workspace module.

Postgres is the target store (see compose.yaml). SQLite is supported for tests
and local smoke runs. When DATABASE_URL is unset the workspace APIs report
CAPABILITY_DISABLED instead of pretending to persist anything.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

_engine = None
_session_factory: sessionmaker | None = None


class Base(DeclarativeBase):
    pass


def database_url() -> str:
    return (os.getenv("DATABASE_URL") or "").strip()


def configured() -> bool:
    return bool(database_url())


def engine():
    global _engine, _session_factory
    url = database_url()
    if not url:
        raise RuntimeError("DATABASE_URL 未配置")
    if _engine is None or str(_engine.url) != url:
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        _engine = create_engine(url, pool_pre_ping=True, future=True, connect_args=connect_args)
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
