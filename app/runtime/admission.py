"""Final admission: serialize, re-read committed history, decide, register.

Why this exists: dedup evidence read *outside* a critical section can be stale. Two
requests generating near-identical copy at the same time can both read a history without
the other and both be admitted — the failure the plan calls out as "并发近似稿不会因同时
读旧历史一起通过".

What happens here, in order:

1. the fingerprint is computed *before* taking the lock (no model, embedding or network
   call is ever made while holding it);
2. a short scope lock is taken, then the authoritative history is **re-read inside it** —
   never a snapshot read before the lock;
3. the comparison is decided and the row registered in the same transaction;
4. the lock is released by committing.

Honest boundaries, because they are easy to overstate:

* **Scope is one workspace.** The plan's ``brand_content_scope_id`` (an *authorised*
  cross-store scope) is not implemented, so this is not brand-wide admission.
* **SQLite serialises all writers on the file**, not per scope. That is fine for a
  single-instance pilot and is *not* a capacity result — it must not be read as "ten
  thousand users will work". The Postgres path uses ``pg_advisory_xact_lock`` as the plan
  specifies, but **it has not been exercised** (no Postgres or Docker in this environment).
* **Nothing is blocked.** Thresholds stay unapplied until calibration, so a finding is
  evidence. When the lock cannot be taken the verdict is ``unverified`` — never a pass.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

from sqlalchemy import text

from .. import db as db_layer
from . import dedup


class LockUnavailable(RuntimeError):
    """The admission lock could not be acquired within the configured timeout."""


def _env_float(name: str, default: float, low: float, high: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return min(high, max(low, value))


def _env_int(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return min(high, max(low, value))


def lock_timeout_seconds() -> float:
    # The plan's policy example uses 2 s.
    return _env_float("LG_ADMISSION_LOCK_TIMEOUT", 2.0, 0.1, 30.0)


def max_rechecks() -> int:
    # The plan's policy example uses 2.
    return _env_int("LG_ADMISSION_RECHECKS", 2, 0, 5)


def _lock_scope_key(workspace_id: str) -> str:
    return "content-scope:" + (workspace_id or "default")


@contextmanager
def _locked_connection(engine, workspace_id: str, timeout: float):
    """A short critical section, dialect aware.

    SQLite uses ``BEGIN IMMEDIATE`` (a RESERVED lock taken up front, so two writers cannot
    both believe they hold the critical section). Postgres uses a transaction-scoped
    advisory lock keyed on the scope, as the plan specifies.
    """
    dialect = engine.dialect.name
    if dialect == "sqlite":
        # One connection for the whole critical section. Taking the lock on one connection
        # and writing on another would put the INSERT outside the lock, which is precisely
        # the race this function exists to remove.
        connection = engine.connect().execution_options(isolation_level="AUTOCOMMIT")
        try:
            connection.exec_driver_sql("PRAGMA busy_timeout = %d" % int(timeout * 1000))
            try:
                connection.exec_driver_sql("BEGIN IMMEDIATE")
            except Exception as error:  # sqlite3.OperationalError: database is locked
                raise LockUnavailable(str(error)) from None
            try:
                yield connection, None
                connection.exec_driver_sql("COMMIT")
            except Exception:
                try:
                    connection.exec_driver_sql("ROLLBACK")
                except Exception:  # noqa: BLE001 - the original error matters more
                    pass
                raise
        finally:
            connection.close()
        return

    # Postgres, per the plan: READ COMMITTED + advisory lock on the authorised scope.
    # NOT EXERCISED in this environment (no Postgres/Docker available).
    with engine.connect() as connection:
        connection.exec_driver_sql("SET LOCAL lock_timeout = '%dms'" % int(timeout * 1000))
        connection.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                           {"key": _lock_scope_key(workspace_id)})
        try:
            yield connection, None
            connection.commit()
        except Exception:
            connection.rollback()
            raise


def _read_history(connection, workspace_id: str, exclude_run_id: str, limit: int) -> list[dict]:
    rows = connection.execute(text(
        "SELECT id, run_id, conversation_id, normalized_sha256, algorithm, minhash "
        "FROM agent_content_fingerprints WHERE workspace_id = :scope AND run_id != :run "
        "ORDER BY created_at DESC LIMIT :limit"),
        {"scope": workspace_id or "default", "run": exclude_run_id,
         "limit": limit}).fetchall()
    history = []
    for row in rows:
        try:
            sketch = json.loads(row[5]) if isinstance(row[5], str) else list(row[5] or [])
        except (TypeError, ValueError):
            sketch = []
        history.append({"id": row[0], "run_id": row[1], "conversation_id": row[2],
                        "normalized_sha256": row[3], "algorithm": row[4],
                        "minhash": sketch})
    return history


def _history_count(engine, workspace_id: str, exclude_run_id: str) -> int:
    try:
        with engine.connect() as connection:
            return int(connection.execute(text(
                "SELECT count(*) FROM agent_content_fingerprints "
                "WHERE workspace_id = :scope AND run_id != :run"),
                {"scope": workspace_id or "default", "run": exclude_run_id}).scalar() or 0)
    except Exception:  # noqa: BLE001 - a count is an optimisation, not a guarantee
        return 0


def _insert_fingerprint(connection, *, conversation_id, run_id, workspace_id, artifact_id,
                        sketch) -> None:
    connection.execute(text(
        "INSERT INTO agent_content_fingerprints "
        "(id, conversation_id, run_id, workspace_id, artifact_id, algorithm, "
        " normalized_sha256, length, shingles, minhash, created_at) "
        "VALUES (:id, :conversation_id, :run_id, :workspace_id, :artifact_id, :algorithm, "
        "        :sha, :length, :shingles, :minhash, :created_at)"),
        {"id": uuid.uuid4().hex[:32], "conversation_id": conversation_id, "run_id": run_id,
         "workspace_id": workspace_id or "default", "artifact_id": artifact_id or "",
         "algorithm": str(sketch.get("algorithm") or ""),
         "sha": str(sketch.get("normalized_sha256") or ""),
         "length": int(sketch.get("length") or 0),
         "shingles": int(sketch.get("shingles") or 0),
         "minhash": json.dumps(list(sketch.get("minhash") or [])),
         "created_at": datetime.now(timezone.utc).isoformat(sep=" ")})


def _base_admission_info(dialect: str) -> dict:
    return {"dialect": dialect, "locked": False, "rechecked": False,
            "stale_snapshot": False, "attempts": 0, "lock_wait_ms": 0,
            "scope": "workspace", "cross_store_authorised": False,
            "blocked": False, "calibrated": False}


def admit_delivery(*, workspace_id: str, conversation_id: str, run_id: str,
                   artifact_id: str, sketch: dict, history_limit: int = 200) -> dict:
    """Take the admission lock, decide on freshly committed history, and register."""
    engine = db_layer.engine()
    dialect = engine.dialect.name
    info = _base_admission_info(dialect)
    timeout = lock_timeout_seconds()
    rechecks = max_rechecks()

    before = _history_count(engine, workspace_id, run_id)
    started = time.monotonic()
    result = None
    for attempt in range(rechecks + 1):
        info["attempts"] = attempt + 1
        try:
            with _locked_connection(engine, workspace_id, timeout) as (connection, _raw):
                history = _read_history(connection, workspace_id, run_id, history_limit)
                result = dedup.compare(sketch, history)
                if len(history) > before:
                    # Another delivery registered while we were deciding: this is exactly
                    # the race the lock exists to remove.
                    info["stale_snapshot"] = True
                _insert_fingerprint(connection, conversation_id=conversation_id,
                                    run_id=run_id, workspace_id=workspace_id,
                                    artifact_id=artifact_id, sketch=sketch)
            info["locked"] = True
            info["lock_wait_ms"] = int((time.monotonic() - started) * 1000)
            info["rechecked"] = bool(info["stale_snapshot"] or attempt > 0)
            return {**result, "admission": info}
        except LockUnavailable:
            continue
        except Exception as exc:  # noqa: BLE001 - admission must not break a delivery
            info["error"] = type(exc).__name__
            break

    # The lock could not be taken. Report the comparison as UNVERIFIED — never as a pass —
    # and still register, so the content is not silently lost from the memory.
    info["locked"] = False
    info["reason"] = "lock_timeout"
    info["lock_wait_ms"] = int((time.monotonic() - started) * 1000)
    fallback = {"mode": dedup.mode(), "algorithm": dedup.ALGORITHM, "kind": "unverified",
                "similarity": 0.0, "exact_match": None, "nearest": None, "compared": 0,
                "calibrated": False, "blocks": False}
    try:
        with engine.connect() as connection:
            # Registration matters more than the verdict, but must still be bounded: give
            # it longer than the lock attempt so transient contention does not silently
            # drop this delivery from the content memory.
            connection.exec_driver_sql(
                "PRAGMA busy_timeout = %d" % int(max(timeout, 5.0) * 1000))
            _insert_fingerprint(connection, conversation_id=conversation_id, run_id=run_id,
                                workspace_id=workspace_id, artifact_id=artifact_id,
                                sketch=sketch)
            connection.commit()
    except Exception as exc:  # noqa: BLE001
        info["register_error"] = type(exc).__name__
    return {**fallback, "admission": info}