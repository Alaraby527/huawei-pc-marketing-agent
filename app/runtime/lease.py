"""Durable leases over runs: claim, heartbeat, expiry, bounded attempts.

The in-process conversation lock is a *local* lease. It says nothing to a second worker and
nothing at all after a restart, so a crash is indistinguishable from a run that is still
working — which is why a restart used to have to assume the worst for every `running` row.

A persisted lease makes the difference observable:

* **held** — a worker owns the run until ``expires_at``; other workers must not touch it.
* **held but past its expiry** — the owner stopped heart-beating, so the run is genuinely
  abandoned and can be reclaimed (within the attempt limit). Expiry is *derived* from
  ``expires_at``; it is not a stored state, and treating it as one is what made a takeover
  condition look satisfied when it never was.
* **released** — the owner finished and said so.

The attempt counter is what keeps recovery bounded: the plan requires finite retries, and a
run that keeps dying must end as a failure rather than be retried forever.
"""
from __future__ import annotations

import os
import socket
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import models

HELD = "held"
RELEASED = "released"
EXPIRED = "expired"

_WORKER = ""


def worker_id() -> str:
    """Stable identity for this process, so a lease can be recognised as our own."""
    global _WORKER
    if not _WORKER:
        _WORKER = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
    return _WORKER


def ttl_seconds() -> float:
    try:
        value = float(os.getenv("LG_LEASE_TTL_SECONDS", "90"))
    except (TypeError, ValueError):
        value = 90.0
    return min(3600.0, max(15.0, value))


def max_attempts() -> int:
    try:
        value = int(os.getenv("LG_RUN_MAX_ATTEMPTS", "2"))
    except (TypeError, ValueError):
        value = 2
    return min(5, max(1, value))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _row(db: Session, run_id: str) -> models.AgentRunLease | None:
    return db.scalars(select(models.AgentRunLease).where(
        models.AgentRunLease.run_id == run_id)).first()


def claim(db: Session, *, run_id: str, conversation_id: str, owner: str | None = None,
          ttl: float | None = None) -> dict:
    """Take the lease for a run, or report why it cannot be taken.

    Reclaiming an expired lease is a single conditional UPDATE, so two workers racing to
    recover the same abandoned run cannot both win.
    """
    me = owner or worker_id()
    lifetime = ttl if ttl is not None else ttl_seconds()
    now = _now()
    expires = now + timedelta(seconds=lifetime)

    row = _row(db, run_id)
    if row is None:
        try:
            # Savepoint: a lost insert race must not poison the caller's transaction.
            with db.begin_nested():
                db.add(models.AgentRunLease(run_id=run_id, conversation_id=conversation_id,
                                            owner=me, state=HELD, attempt=1,
                                            acquired_at=now, heartbeat_at=now,
                                            expires_at=expires))
                db.flush()
            return {"claimed": True, "attempt": 1, "owner": me, "reason": "acquired"}
        except IntegrityError:
            row = _row(db, run_id)
            if row is None:
                return {"claimed": False, "reason": "claim_race_lost"}

    if row.state == HELD and row.expires_at and row.expires_at.replace(
            tzinfo=timezone.utc) > now:
        if row.owner == me:
            return {"claimed": True, "attempt": row.attempt, "owner": me,
                    "reason": "already_owned"}
        return {"claimed": False, "reason": "held_by_another_worker", "owner": row.owner,
                "expires_at": row.expires_at.isoformat()}

    if row.attempt >= max_attempts():
        return {"claimed": False, "reason": "attempts_exhausted", "attempt": row.attempt,
                "max_attempts": max_attempts()}

    # "Expired" is derived from expires_at, not a stored state, so the takeover condition
    # must accept a row that is still `held` but past its expiry. Requiring state != held
    # would make abandoned runs unrecoverable.
    taken = db.execute(
        models.AgentRunLease.__table__.update()
        .where(models.AgentRunLease.run_id == run_id,
               or_(models.AgentRunLease.state != HELD,
                   models.AgentRunLease.expires_at <= now))
        .values(owner=me, state=HELD, attempt=models.AgentRunLease.attempt + 1,
                acquired_at=now, heartbeat_at=now, expires_at=expires, released_at=None,
                reason="")
        .returning(models.AgentRunLease.attempt))
    # Read the value the statement wrote. A follow-up SELECT would answer from the identity
    # map with the pre-increment object, which is how this reported attempt 1 instead of 2.
    new_attempt = taken.scalar()
    if new_attempt is None:
        # Somebody else reclaimed it between the read and this update.
        return {"claimed": False, "reason": "reclaim_race_lost"}
    return {"claimed": True, "attempt": int(new_attempt), "owner": me,
            "reason": "reclaimed_expired"}


def heartbeat(db: Session, *, run_id: str, owner: str | None = None,
              ttl: float | None = None) -> bool:
    """Extend our own lease. Another worker's lease is never extended."""
    me = owner or worker_id()
    lifetime = ttl if ttl is not None else ttl_seconds()
    now = _now()
    updated = db.execute(
        models.AgentRunLease.__table__.update()
        .where(models.AgentRunLease.run_id == run_id,
               models.AgentRunLease.owner == me,
               models.AgentRunLease.state == HELD)
        .values(heartbeat_at=now, expires_at=now + timedelta(seconds=lifetime)))
    return bool(updated.rowcount)


def release(db: Session, *, run_id: str, owner: str | None = None,
            reason: str = "finished") -> bool:
    me = owner or worker_id()
    updated = db.execute(
        models.AgentRunLease.__table__.update()
        .where(models.AgentRunLease.run_id == run_id,
               models.AgentRunLease.owner == me,
               models.AgentRunLease.state == HELD)
        .values(state=RELEASED, released_at=_now(), reason=reason[:120]))
    return bool(updated.rowcount)


def describe(db: Session, run_id: str) -> dict | None:
    row = _row(db, run_id)
    if row is None:
        return None
    now = _now()
    expires = row.expires_at.replace(tzinfo=timezone.utc) if row.expires_at else None
    return {"owner": row.owner, "state": row.state, "attempt": row.attempt,
            "expires_at": expires.isoformat() if expires else None,
            "heartbeat_at": row.heartbeat_at.isoformat() if row.heartbeat_at else None,
            "expired": bool(expires and expires <= now and row.state == HELD),
            "is_ours": row.owner == worker_id(), "reason": row.reason}


def single_worker() -> bool:
    """Whether this deployment runs one worker, which changes what a restart can conclude.

    A TTL alone cannot tell a crashed worker from a live one on another host, so a
    multi-worker deployment must wait for expiry. A single-worker deployment knows
    something stronger: it just started, so any lease it does not own belongs to a process
    that no longer exists. Set ``LG_LEASE_SINGLE_WORKER=0`` when that stops being true.
    """
    return str(os.getenv("LG_LEASE_SINGLE_WORKER", "1")).strip().lower() not in ("0", "false",
                                                                               "no")


def reclaim_stale_on_start(db: Session, *, assume_single_worker: bool | None = None) -> int:
    """Expire leases left by a previous process when this deployment is single-worker."""
    single = single_worker() if assume_single_worker is None else assume_single_worker
    if not single:
        return 0
    me = worker_id()
    now = _now()
    updated = db.execute(
        models.AgentRunLease.__table__.update()
        .where(models.AgentRunLease.state == HELD,
               models.AgentRunLease.owner != me)
        .values(state=RELEASED, released_at=now, reason="previous_process_gone"))
    return int(updated.rowcount or 0)


def abandoned(db: Session, ttl: float | None = None) -> list[str]:
    """Runs whose lease is held past its expiry — i.e. their worker stopped beating.

    A run with no lease row at all is *not* reported here: that is the pre-lease state and
    the caller decides what to do with it.
    """
    now = _now()
    rows = db.scalars(select(models.AgentRunLease).where(
        models.AgentRunLease.state == HELD,
        models.AgentRunLease.expires_at <= now)).all()
    return [row.run_id for row in rows]