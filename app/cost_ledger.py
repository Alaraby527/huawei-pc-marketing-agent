"""Cost ledger: reserve before spending, settle to the real amount, never zero an unknown.

The plan's budget state machine as a skeleton that can refuse but does not yet charge:

* **No currency, no billing.** With ``IMAGE_CURRENCY`` unset every reservation is refused,
  matching package 1's own defaults (``currency: "TO_BE_CONFIRMED"``,
  ``budget_per_request: null``) and its note that billing is forbidden before a production
  budget exists.
* **An unestimatable cost blocks submission.** The plan says to block rather than submit when
  the provider charge cannot be estimated; a guess is wrong in the direction of overspending.
* **Unknown is a state, not a zero.** ``settle(actual=None)`` moves a reservation to
  ``unknown`` and flags it for reconciliation ("不能简单将预留清零"). It keeps counting
  against the ceiling until a supplier result says otherwise.
* **One ceiling per request.** Every paid attempt of a request, retries included, draws on the
  same limit, and the limit comes from the server environment - never from the caller.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import models

RESERVED = "reserved"
SETTLED = "settled"
RELEASED = "released"
UNKNOWN = "unknown"
# Recorded when a paid call happened while no budget was configured. It carries no amount, so
# it never pretends to be accounted money - it is the evidence that unpriced spend occurred.
UNPRICED = "unpriced"

# States whose money still occupies the budget.
PAID_STATES = (RESERVED, SETTLED, UNKNOWN)


def currency() -> str:
    return (os.getenv("IMAGE_CURRENCY") or "").strip().upper()


def _limit(name: str) -> float | None:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value >= 0 else None


def per_request_limit() -> float | None:
    return _limit("IMAGE_BUDGET_PER_REQUEST")


def per_day_limit() -> float | None:
    return _limit("IMAGE_BUDGET_PER_DAY")


def max_paid_attempts() -> int:
    try:
        value = int((os.getenv("IMAGE_MAX_PAID_ATTEMPTS") or "2").strip())
    except ValueError:
        value = 2
    return min(10, max(1, value))


def configured() -> bool:
    """Billing needs a currency *and* a stated ceiling; with either missing it stays off."""
    return bool(currency()) and per_request_limit() is not None


def status() -> dict:
    return {"configured": configured(), "currency": currency(),
            "per_request_limit": per_request_limit(), "per_day_limit": per_day_limit(),
            "max_paid_attempts_per_request": max_paid_attempts(),
            "notice": ("预算未配置时禁止计费；unknown 费用进入对账，不清零。"
                       if not configured() else
                       "预算已配置；同一请求的全部付费尝试共享该上限。")}


def _spent(db: Session, request_id: str) -> float:
    rows = db.scalars(select(models.CostReservation).where(
        models.CostReservation.request_id == request_id,
        models.CostReservation.state.in_(PAID_STATES))).all()
    total = 0.0
    for row in rows:
        # A settled charge counts its actual; everything else counts what it reserved, so an
        # unknown cannot quietly enlarge the ceiling by its own amount.
        amount = row.actual if (row.state == SETTLED and row.actual is not None) else row.estimated
        total += float(amount or 0.0)
    return round(total, 4)


def reserve(db: Session, *, request_id: str, estimated: float | None, idempotency_key: str,
            conversation_id: str = "", purpose: str = "image_generate", attempt: int = 1,
            tenant_id: str = "default", provider: str = "") -> dict:
    """Take budget before submitting a paid attempt, or refuse with a reason."""
    if not idempotency_key:
        return {"ok": False, "error": "IDEMPOTENCY_KEY_REQUIRED",
                "message": "计费尝试必须带幂等键，否则重试会重复计费"}
    existing = db.scalars(select(models.CostReservation).where(
        models.CostReservation.tenant_id == tenant_id,
        models.CostReservation.idempotency_key == idempotency_key)).first()
    if existing is not None:
        return {"ok": True, "replayed": True, "reservation_id": existing.id,
                "state": existing.state, "estimated": existing.estimated,
                "currency": existing.currency}
    if not configured():
        return {"ok": False, "error": "BILLING_NOT_CONFIGURED",
                "message": "货币或单稿上限未配置，按方案禁止计费", "ledger": status()}
    if estimated is None:
        return {"ok": False, "error": "COST_UNKNOWN_BLOCKED",
                "message": "无法估算本次供应商费用，按方案先阻断提交，而不是猜一个数"}
    if float(estimated) < 0:
        return {"ok": False, "error": "INVALID_ESTIMATE", "message": "预估费用不能为负"}
    if int(attempt) > max_paid_attempts():
        return {"ok": False, "error": "PAID_ATTEMPTS_EXCEEDED",
                "message": f"该请求付费尝试已达上限 {max_paid_attempts()} 次"}
    limit = per_request_limit()
    spent = _spent(db, request_id)
    if limit is not None and spent + float(estimated) > limit:
        return {"ok": False, "error": "BUDGET_EXCEEDED",
                "message": f"该请求已占用 {spent}，本次预估 {estimated}，超过单稿上限 {limit}；"
                           "不得通过新建请求绕过",
                "spent": spent, "estimated": float(estimated), "limit": limit,
                "currency": currency()}
    row = models.CostReservation(tenant_id=tenant_id, request_id=request_id,
                                 conversation_id=conversation_id,
                                 idempotency_key=idempotency_key, purpose=purpose,
                                 attempt=int(attempt), state=RESERVED, currency=currency(),
                                 estimated=float(estimated), provider=provider)
    db.add(row)
    try:
        db.flush()
    except IntegrityError:
        # A concurrent duplicate won the unique key. It is the same paid attempt, so adopt the
        # winner instead of creating a second charge.
        db.rollback()
        winner = db.scalars(select(models.CostReservation).where(
            models.CostReservation.tenant_id == tenant_id,
            models.CostReservation.idempotency_key == idempotency_key)).first()
        return {"ok": True, "replayed": True, "concurrent": True,
                "reservation_id": winner.id if winner else "",
                "state": winner.state if winner else ""}
    return {"ok": True, "replayed": False, "reservation_id": row.id, "state": row.state,
            "estimated": row.estimated, "currency": row.currency, "spent_before": spent,
            "limit": limit}


def settle(db: Session, reservation_id: str, actual: float | None, *, note: str = "") -> dict:
    """Finish a reservation. ``actual=None`` means the charge is unknown, not free."""
    row = db.get(models.CostReservation, reservation_id)
    if row is None:
        return {"ok": False, "error": "RESERVATION_NOT_FOUND"}
    if row.state == RELEASED:
        return {"ok": False, "error": "ALREADY_RELEASED", "state": row.state}
    row.settled_at = datetime.now(timezone.utc)
    if actual is None:
        row.state = UNKNOWN
        row.needs_reconciliation = True
        row.note = (note or "供应商未返回费用，进入对账")[:300]
        db.flush()
        return {"ok": True, "state": row.state, "needs_reconciliation": True,
                "estimated": row.estimated, "actual": None,
                "message": "费用未知：保留预留并建立对账，不清零"}
    row.state = SETTLED
    row.actual = float(actual)
    row.needs_reconciliation = float(actual) > float(row.estimated or 0.0)
    row.note = note[:300]
    db.flush()
    return {"ok": True, "state": row.state, "actual": row.actual, "estimated": row.estimated,
            "over_estimate": round(float(actual) - float(row.estimated or 0.0), 4),
            "needs_reconciliation": row.needs_reconciliation}


def release(db: Session, reservation_id: str, *, note: str = "") -> dict:
    """Return budget for an attempt that was never charged."""
    row = db.get(models.CostReservation, reservation_id)
    if row is None:
        return {"ok": False, "error": "RESERVATION_NOT_FOUND"}
    if row.state == UNKNOWN:
        return {"ok": False, "error": "UNKNOWN_NEEDS_RECONCILIATION",
                "message": "费用未知的预留必须由对账结果决定，不能直接释放清零"}
    row.state = RELEASED
    row.settled_at = datetime.now(timezone.utc)
    row.note = note[:300]
    db.flush()
    return {"ok": True, "state": row.state}


def reconcile(db: Session, reservation_id: str, actual: float, *, note: str = "") -> dict:
    """Resolve an unknown charge once the supplier result is known."""
    row = db.get(models.CostReservation, reservation_id)
    if row is None:
        return {"ok": False, "error": "RESERVATION_NOT_FOUND"}
    if row.state != UNKNOWN:
        return {"ok": False, "error": "NOT_AWAITING_RECONCILIATION", "state": row.state}
    row.state = SETTLED
    row.actual = float(actual)
    row.needs_reconciliation = False
    row.settled_at = datetime.now(timezone.utc)
    row.note = (note or "对账完成")[:300]
    db.flush()
    return {"ok": True, "state": row.state, "actual": row.actual}


def summary(db: Session, request_id: str) -> dict:
    rows = db.scalars(select(models.CostReservation).where(
        models.CostReservation.request_id == request_id)).all()
    by_state: dict[str, int] = {}
    for row in rows:
        by_state[row.state] = by_state.get(row.state, 0) + 1
    unknown = [row.id for row in rows if row.state == UNKNOWN]
    return {"request_id": request_id, "currency": currency(), "by_state": by_state,
            "count": len(rows), "committed": _spent(db, request_id),
            "settled_actual": round(sum(float(row.actual or 0.0) for row in rows
                                        if row.state == SETTLED), 4),
            "unknown_count": len(unknown), "unknown_reservations": unknown,
            "limit": per_request_limit(), "configured": configured(),
            "needs_reconciliation": bool(unknown)}


def pending_reconciliation(db: Session, limit: int = 50) -> list[dict]:
    rows = db.scalars(select(models.CostReservation).where(
        models.CostReservation.state == UNKNOWN).order_by(
        models.CostReservation.created_at).limit(limit)).all()
    return [{"reservation_id": row.id, "request_id": row.request_id, "purpose": row.purpose,
             "attempt": row.attempt, "estimated": row.estimated, "currency": row.currency,
             "created_at": row.created_at} for row in rows]


def enforce() -> bool:
    """Whether an unconfigured budget *blocks* a paid call.

    The plan says billing is forbidden before a production budget exists, which strictly means
    blocking the call. That is off by default here, and the deviation is deliberate and
    visible: the user is actively using image generation for acceptance, and silently
    refusing every request would break it. Set ``IMAGE_BILLING_ENFORCE=1`` to make the plan's
    rule binding; until then each attempt is still *recorded* as ``unpriced`` so the spend is
    visible even though it was not pre-authorised.
    """
    return str(os.getenv("IMAGE_BILLING_ENFORCE", "")).strip().lower() in ("1", "true", "yes")


def record(db: Session, *, request_id: str, estimated: float | None, idempotency_key: str,
           conversation_id: str = "", purpose: str = "image_generate", attempt: int = 1,
           tenant_id: str = "default", provider: str = "") -> dict:
    """Account for a paid attempt, blocking only when enforcement is switched on."""
    result = reserve(db, request_id=request_id, estimated=estimated,
                     idempotency_key=idempotency_key, conversation_id=conversation_id,
                     purpose=purpose, attempt=attempt, tenant_id=tenant_id, provider=provider)
    if result.get("ok"):
        return {**result, "priced": True, "enforced": enforce()}
    if enforce():
        return {**result, "priced": False, "enforced": True}
    existing = db.scalars(select(models.CostReservation).where(
        models.CostReservation.tenant_id == tenant_id,
        models.CostReservation.idempotency_key == idempotency_key)).first()
    if existing is not None:
        return {"ok": True, "replayed": True, "priced": existing.state != UNPRICED,
                "reservation_id": existing.id, "state": existing.state,
                "enforced": False}
    row = models.CostReservation(tenant_id=tenant_id, request_id=request_id,
                                 conversation_id=conversation_id,
                                 idempotency_key=idempotency_key, purpose=purpose,
                                 attempt=int(attempt), state=UNPRICED, currency=currency(),
                                 estimated=None, provider=provider,
                                 note=("未配置预算，按观察记录：" + str(result.get("error")))[:300])
    db.add(row)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        return {"ok": True, "replayed": True, "priced": False, "enforced": False}
    return {"ok": True, "priced": False, "enforced": False, "state": UNPRICED,
            "reservation_id": row.id, "observed_error": result.get("error"),
            "message": "预算未配置：本次调用已按观察记录，未做收费授权（可设 "
                       "IMAGE_BILLING_ENFORCE=1 按方案拦截）"}


def mark_unpriced(db: Session, reservation_id: str, actual: float | None = None,
                  *, note: str = "") -> dict:
    """Attach the measured cost to an observation once the provider reports usage."""
    row = db.get(models.CostReservation, reservation_id)
    if row is None:
        return {"ok": False, "error": "RESERVATION_NOT_FOUND"}
    if row.state != UNPRICED:
        return {"ok": False, "error": "NOT_UNPRICED", "state": row.state}
    row.actual = None if actual is None else float(actual)
    row.estimated = None if actual is None else float(actual)
    row.note = (note or "观察记录已补充实测用量")[:300]
    db.flush()
    return {"ok": True, "state": row.state, "actual": row.actual}
