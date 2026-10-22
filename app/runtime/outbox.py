"""Reliable handoff events (plan T16).

Two properties, both required by the plan:

* the event is written **in the same transaction** as the content registration, so a
  delivery can never exist without its handoff record, or vice versa;
* delivery happens separately with **bounded retries**, so an unavailable consumer delays
  the event instead of losing it. An event that exhausts its attempts stays visible as
  ``failed`` with its error — it is never quietly discarded.

What this deliberately does not do: push to any external system. There is no CRM, publishing
or analytics integration in this codebase, so the default handler records the handoff as
delivered-to-inbox (a durable row an operator surface can read) and nothing more. Claiming a
real external handoff would be a lie about what is connected.
"""
from __future__ import annotations

import os

from sqlalchemy import text
from sqlalchemy.orm import Session

from .. import models

PENDING = "pending"
DELIVERED = "delivered"
FAILED = "failed"

CONTENT_REGISTERED = "content_registered"


def max_attempts() -> int:
    try:
        value = int(os.getenv("LG_OUTBOX_MAX_ATTEMPTS", "3"))
    except (TypeError, ValueError):
        value = 3
    return min(10, max(1, value))


def enqueue(connection, *, conversation_id: str, run_id: str, workspace_id: str,
            event_type: str, payload: dict) -> None:
    """Insert an event on the caller's connection, inside the caller's transaction.

    Taking a connection rather than a Session is deliberate: this must run inside the
    admission lock's transaction, not on a separate one.
    """
    import json
    import uuid
    from datetime import datetime, timezone

    connection.execute(text(
        "INSERT INTO agent_outbox_events "
        "(id, conversation_id, run_id, workspace_id, event_type, payload, state, attempts, "
        " last_error, created_at, delivered_at) "
        "VALUES (:id, :conversation_id, :run_id, :workspace_id, :event_type, :payload, "
        "        :state, 0, '', :created_at, NULL)"),
        {"id": uuid.uuid4().hex[:32], "conversation_id": conversation_id, "run_id": run_id,
         "workspace_id": workspace_id or "default", "event_type": event_type,
         "payload": json.dumps(payload or {}, ensure_ascii=False), "state": PENDING,
         "created_at": datetime.now(timezone.utc).isoformat(sep=" ")})


def pending(db: Session, limit: int = 50) -> list[models.AgentOutboxEvent]:
    return list(db.query(models.AgentOutboxEvent).filter(
        models.AgentOutboxEvent.state == PENDING).order_by(
        models.AgentOutboxEvent.created_at).limit(limit).all())


def drain(db: Session, handler=None, limit: int = 50) -> dict:
    """Deliver pending events with a bounded number of attempts.

    ``handler`` receives one event and may raise; a raising handler counts as a failed
    attempt and the event stays pending until its attempts run out.
    """
    handler = handler or _default_handler
    counters = {"delivered": 0, "retried": 0, "failed": 0}
    for event in pending(db, limit):
        try:
            handler(event)
        except Exception as error:  # noqa: BLE001 - a failed handoff is recorded, not raised
            event.attempts += 1
            event.last_error = type(error).__name__ + ": " + str(error)[:160]
            if event.attempts >= max_attempts():
                event.state = FAILED
                counters["failed"] += 1
            else:
                counters["retried"] += 1
            continue
        event.state = DELIVERED
        event.attempts += 1
        event.last_error = ""
        event.delivered_at = models._now()
        counters["delivered"] += 1
    db.flush()
    return counters


def _default_handler(event: models.AgentOutboxEvent) -> None:
    """No external consumer exists in this codebase, so the row itself is the handoff."""
    return None


def stats(db: Session) -> dict:
    rows = db.query(models.AgentOutboxEvent.state).all()
    counts: dict[str, int] = {}
    for (state,) in rows:
        counts[state] = counts.get(state, 0) + 1
    return {"total": len(rows), "by_state": counts, "max_attempts": max_attempts()}
