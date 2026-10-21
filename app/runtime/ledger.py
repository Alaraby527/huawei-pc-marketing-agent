"""Business ledgers for the LangGraph runtime.

Three separate concerns, deliberately not collapsed into the checkpointer:

* **Request idempotency** — scoped to ``(conversation_id, key)`` and bound to a
  normalized payload hash. Sending the same key with different content is a conflict,
  not a silent replay. The previous global ``AgentRun.idempotency_key`` lookup could
  collide across conversations and never compared payloads.
* **Effect ledger** — makes a business write replayable by ``call_uid`` after a crash
  between the business commit and the checkpoint write, so recovery does not create a
  duplicate artifact revision.
* **Usage ledger** — one row per real model request. Missing usage is *unknown*
  (NULL), never a measured zero.

The checkpointer is an execution record, not a distributed exactly-once guarantee.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import models

EFFECT_PENDING = "pending"
EFFECT_COMPLETED = "completed"
EFFECT_SENT_UNKNOWN = "sent_outcome_unknown"
EFFECT_FAILED = "failed"


class RequestConflict(RuntimeError):
    """Same idempotency key reused with a different payload."""


def payload_hash(value: Any) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def fresh_run_status(run_id: str) -> str:
    """Read a run's status on its own short transaction.

    ``db.get()`` on the request's session returns the identity-mapped object, so a
    cancel written and committed by another request is invisible and the in-flight run
    keeps going. Cancellation is a persisted intent; it must be read from the database,
    not from whatever this session happened to load earlier.
    """
    from .. import db as db_layer
    try:
        session = db_layer.session_factory()()
    except RuntimeError:
        return ""
    try:
        return str(session.execute(
            select(models.AgentRun.status).where(models.AgentRun.id == run_id)).scalar() or "")
    except Exception:  # noqa: BLE001 - a status probe must never break the run
        return ""
    finally:
        session.close()


def next_seq(db: Session, conversation_id: str) -> int:
    """Server-side monotonic message sequence.

    The legacy runtime derived ``seq`` from a per-batch formula, which produced
    collisions between a batch's last tool result and the next assistant turn.
    """
    current = db.scalar(select(func.max(models.AgentMessage.seq)).where(
        models.AgentMessage.conversation_id == conversation_id))
    return int(current or 0) + 1


def accept_request(db: Session, conversation_id: str, key: str,
                   request_hash: str) -> tuple[str, bool]:
    """Return ``(run_id, replayed)`` for a scoped request key.

    Raises :class:`RequestConflict` when the key was already used with other content.
    """
    if not key:
        return "", False
    row = db.scalars(select(models.AgentRequestRecord).where(
        models.AgentRequestRecord.conversation_id == conversation_id,
        models.AgentRequestRecord.request_key == key)).first()
    if row:
        if row.payload_hash != request_hash:
            raise RequestConflict("同一请求键对应了不同内容")
        return row.run_id, True
    record = models.AgentRequestRecord(conversation_id=conversation_id, request_key=key,
                                       payload_hash=request_hash)
    db.add(record)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        row = db.scalars(select(models.AgentRequestRecord).where(
            models.AgentRequestRecord.conversation_id == conversation_id,
            models.AgentRequestRecord.request_key == key)).first()
        if row is None or row.payload_hash != request_hash:
            raise RequestConflict("同一请求键对应了不同内容") from None
        return row.run_id, True
    return "", False


def bind_request_run(db: Session, conversation_id: str, key: str, run_id: str) -> None:
    if not key:
        return
    row = db.scalars(select(models.AgentRequestRecord).where(
        models.AgentRequestRecord.conversation_id == conversation_id,
        models.AgentRequestRecord.request_key == key)).first()
    if row is not None:
        row.run_id = run_id
        db.flush()


def record_attempt(db: Session, *, conversation_id: str, run_id: str, decision_index: int,
                   kind: str, model_id: str, protocol_mode: str, usage: dict | None,
                   latency_ms: int, ok: bool = True, error: str = "") -> models.AgentModelAttempt:
    """Persist one real model request.

    ``usage`` missing or incomplete is stored as NULL with certainty ``unknown``:
    a provider that did not report tokens has not reported zero tokens.
    """
    raw = usage or {}
    prompt = raw.get("prompt_tokens")
    completion = raw.get("completion_tokens")
    known = prompt is not None and completion is not None
    attempt = models.AgentModelAttempt(
        conversation_id=conversation_id, run_id=run_id, decision_index=int(decision_index),
        kind=kind, model_id=model_id, protocol_mode=protocol_mode,
        prompt_tokens=int(prompt) if isinstance(prompt, (int, float)) else None,
        completion_tokens=int(completion) if isinstance(completion, (int, float)) else None,
        usage_certainty="reported" if known else "unknown",
        latency_ms=int(latency_ms), ok=bool(ok), error=str(error)[:200])
    db.add(attempt)
    db.flush()
    return attempt


def usage_totals(db: Session, conversation_id: str) -> dict[str, Any]:
    """Aggregate usage while keeping unknown separate from a measured zero."""
    rows = db.scalars(select(models.AgentModelAttempt).where(
        models.AgentModelAttempt.conversation_id == conversation_id)).all()
    known = [row for row in rows if row.usage_certainty == "reported"]
    return {
        "attempts": len(rows),
        "measured_attempts": len(known),
        "unknown_attempts": len(rows) - len(known),
        "input_tokens": sum(int(row.prompt_tokens or 0) for row in known),
        "output_tokens": sum(int(row.completion_tokens or 0) for row in known),
        "usage_certainty": "reported" if known and len(known) == len(rows) else (
            "unknown" if not known else "partial"),
    }


# ------------------------------------------------------------------ effect ledger

def effect_lookup(db: Session, call_uid: str, request_hash: str) -> dict[str, Any] | None:
    """Return a prior outcome for this call, or ``None`` when it may run once.

    Raises :class:`RequestConflict` if the same call id carries different arguments.
    """
    row = db.scalars(select(models.AgentEffect).where(
        models.AgentEffect.call_uid == call_uid)).first()
    if row is None:
        return None
    if row.payload_hash and request_hash and row.payload_hash != request_hash:
        raise RequestConflict("同一调用标识对应了不同参数")
    if row.state == EFFECT_COMPLETED:
        return dict(row.outcome or {})
    if row.state == EFFECT_SENT_UNKNOWN:
        return {"ok": False, "error": "OUTCOME_UNKNOWN", "retryable": False,
                "message": "上一次调用已发出但结果未知；未自动重复执行"}
    return None


def effect_begin(db: Session, *, conversation_id: str, run_id: str, call_uid: str,
                 tool_name: str, request_hash: str) -> models.AgentEffect:
    row = models.AgentEffect(conversation_id=conversation_id, run_id=run_id, call_uid=call_uid,
                             tool_name=tool_name, payload_hash=request_hash,
                             state=EFFECT_PENDING, outcome={})
    db.add(row)
    db.flush()
    return row


def effect_complete(db: Session, call_uid: str, outcome: dict, *,
                    state: str = EFFECT_COMPLETED) -> None:
    """Record a tool outcome. Pass ``state=EFFECT_SENT_UNKNOWN`` when an upstream call
    was dispatched but its result cannot be confirmed — :func:`effect_lookup` then
    refuses to replay it, and the run is reported as needing reconciliation."""
    row = db.scalars(select(models.AgentEffect).where(
        models.AgentEffect.call_uid == call_uid)).first()
    if row is None:
        return
    row.state = state
    row.outcome = outcome or {}
    db.flush()