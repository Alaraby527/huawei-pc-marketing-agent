"""Cost ledger: refusal is the default until a budget exists, and unknown is never free."""
import pytest

from app import cost_ledger as ledger
from app import db as db_layer


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'cost.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    for name in ("IMAGE_CURRENCY", "IMAGE_BUDGET_PER_REQUEST", "IMAGE_BUDGET_PER_DAY",
                 "IMAGE_MAX_PAID_ATTEMPTS"):
        monkeypatch.delenv(name, raising=False)
    db_layer.reset_for_tests(url)
    db_layer.init_db()
    yield
    db_layer.reset_for_tests(url)


def _configured(monkeypatch, currency="CNY", per_request="10"):
    monkeypatch.setenv("IMAGE_CURRENCY", currency)
    monkeypatch.setenv("IMAGE_BUDGET_PER_REQUEST", per_request)


def _reserve(session, **kwargs):
    params = {"request_id": "req-1", "estimated": 1.0, "idempotency_key": "key-0001"}
    params.update(kwargs)
    return ledger.reserve(session, **params)


def test_nothing_is_billable_until_a_budget_is_configured():
    """Package 1 ships currency TO_BE_CONFIRMED and forbids billing before a budget exists."""
    session = db_layer.session_factory()()
    assert ledger.configured() is False
    result = _reserve(session)
    assert result["ok"] is False
    assert result["error"] == "BILLING_NOT_CONFIGURED"
    assert result["ledger"]["configured"] is False
    session.close()


def test_an_unestimatable_cost_blocks_submission(monkeypatch):
    """Guessing the charge is wrong in the direction of overspending."""
    _configured(monkeypatch)
    session = db_layer.session_factory()()
    result = _reserve(session, estimated=None)
    assert result["ok"] is False
    assert result["error"] == "COST_UNKNOWN_BLOCKED"
    assert "阻断提交" in result["message"]
    session.close()


def test_a_paid_attempt_requires_an_idempotency_key(monkeypatch):
    _configured(monkeypatch)
    session = db_layer.session_factory()()
    result = _reserve(session, idempotency_key="")
    assert result["ok"] is False and result["error"] == "IDEMPOTENCY_KEY_REQUIRED"
    session.close()


def test_a_reservation_is_taken_and_settled_to_the_real_amount(monkeypatch):
    _configured(monkeypatch)
    session = db_layer.session_factory()()
    reserved = _reserve(session, estimated=1.5)
    assert reserved["ok"] and reserved["state"] == "reserved"
    settled = ledger.settle(session, reserved["reservation_id"], 1.2)
    assert settled["state"] == "settled" and settled["actual"] == 1.2
    assert settled["needs_reconciliation"] is False
    assert ledger.summary(session, "req-1")["settled_actual"] == 1.2
    session.close()


def test_an_unknown_charge_is_kept_and_counted_not_zeroed(monkeypatch):
    """'不能简单将预留清零' - the ceiling must keep counting money we cannot account for."""
    _configured(monkeypatch, per_request="3")
    session = db_layer.session_factory()()
    reserved = _reserve(session, estimated=2.0)
    unknown = ledger.settle(session, reserved["reservation_id"], None)
    assert unknown["state"] == "unknown"
    assert unknown["needs_reconciliation"] is True
    assert unknown["actual"] is None

    release = ledger.release(session, reserved["reservation_id"])
    assert release["ok"] is False
    assert release["error"] == "UNKNOWN_NEEDS_RECONCILIATION"

    summary = ledger.summary(session, "req-1")
    assert summary["committed"] == 2.0, "未知费用仍占用预算"
    assert summary["needs_reconciliation"] is True
    assert ledger.pending_reconciliation(session)[0]["reservation_id"] == reserved["reservation_id"]
    session.close()


def test_reconciliation_resolves_an_unknown(monkeypatch):
    _configured(monkeypatch)
    session = db_layer.session_factory()()
    reserved = _reserve(session, estimated=2.0)
    ledger.settle(session, reserved["reservation_id"], None)
    fixed = ledger.reconcile(session, reserved["reservation_id"], 2.4)
    assert fixed["state"] == "settled" and fixed["actual"] == 2.4
    assert ledger.pending_reconciliation(session) == []
    assert ledger.summary(session, "req-1")["committed"] == 2.4
    session.close()


def test_the_per_request_ceiling_is_shared_and_cannot_be_bypassed(monkeypatch):
    _configured(monkeypatch, per_request="3")
    session = db_layer.session_factory()()
    assert _reserve(session, estimated=2.0)["ok"] is True
    second = _reserve(session, estimated=2.0, idempotency_key="key-0002")
    assert second["ok"] is False
    assert second["error"] == "BUDGET_EXCEEDED"
    assert second["spent"] == 2.0 and second["limit"] == 3.0
    assert "不得通过新建请求绕过" in second["message"]
    session.close()


def test_a_new_request_id_does_not_touch_another_requests_budget(monkeypatch):
    """The ceiling is per request by design; raising it is a server-side decision."""
    _configured(monkeypatch, per_request="3")
    session = db_layer.session_factory()()
    assert _reserve(session, estimated=3.0)["ok"] is True
    other = _reserve(session, request_id="req-2", estimated=3.0, idempotency_key="key-0003")
    assert other["ok"] is True
    assert ledger.summary(session, "req-1")["committed"] == 3.0
    assert ledger.summary(session, "req-2")["committed"] == 3.0
    session.close()


def test_the_same_key_replays_instead_of_charging_twice(monkeypatch):
    _configured(monkeypatch)
    session = db_layer.session_factory()()
    first = _reserve(session, estimated=1.0)
    again = _reserve(session, estimated=1.0)
    assert again["ok"] is True and again["replayed"] is True
    assert again["reservation_id"] == first["reservation_id"]
    assert ledger.summary(session, "req-1")["count"] == 1
    session.close()


def test_paid_attempts_are_capped_per_request(monkeypatch):
    _configured(monkeypatch, per_request="100")
    monkeypatch.setenv("IMAGE_MAX_PAID_ATTEMPTS", "2")
    session = db_layer.session_factory()()
    assert _reserve(session, attempt=1, idempotency_key="k-1")["ok"] is True
    assert _reserve(session, attempt=2, idempotency_key="k-2")["ok"] is True
    third = _reserve(session, attempt=3, idempotency_key="k-3")
    assert third["ok"] is False and third["error"] == "PAID_ATTEMPTS_EXCEEDED"
    session.close()


def test_releasing_an_uncharged_reservation_returns_the_budget(monkeypatch):
    _configured(monkeypatch, per_request="3")
    session = db_layer.session_factory()()
    reserved = _reserve(session, estimated=3.0)
    assert ledger.release(session, reserved["reservation_id"])["state"] == "released"
    assert ledger.summary(session, "req-1")["committed"] == 0.0
    assert _reserve(session, estimated=3.0, idempotency_key="key-0009")["ok"] is True
    session.close()


def test_an_over_estimate_is_flagged_for_reconciliation(monkeypatch):
    _configured(monkeypatch)
    session = db_layer.session_factory()()
    reserved = _reserve(session, estimated=1.0)
    settled = ledger.settle(session, reserved["reservation_id"], 1.8)
    assert settled["over_estimate"] == 0.8
    assert settled["needs_reconciliation"] is True, "实际超出预估需要人工对账"
    session.close()


def test_a_negative_estimate_is_refused(monkeypatch):
    _configured(monkeypatch)
    session = db_layer.session_factory()()
    result = _reserve(session, estimated=-1)
    assert result["ok"] is False and result["error"] == "INVALID_ESTIMATE"
    session.close()


def test_status_explains_the_refusal_before_anything_is_attempted():
    state = ledger.status()
    assert state["configured"] is False
    assert "禁止计费" in state["notice"]
    assert state["max_paid_attempts_per_request"] == 2

# ---------------------------------------------------------------- observation mode

def test_an_unconfigured_budget_records_an_observation_instead_of_blocking(monkeypatch):
    """The plan says block; blocking is off by default for the user's acceptance run.

    The deviation is deliberate and visible, and the spend is still recorded as unpriced so
    it cannot be mistaken for accounted money.
    """
    session = db_layer.session_factory()()
    result = ledger.record(session, request_id="req-1", estimated=None,
                           idempotency_key="img-1", purpose="studio_image")
    assert result["ok"] is True
    assert result["priced"] is False and result["enforced"] is False
    assert result["state"] == ledger.UNPRICED
    assert "IMAGE_BILLING_ENFORCE" in result["message"]
    session.close()


def test_enforcement_makes_the_plans_rule_binding(monkeypatch):
    monkeypatch.setenv("IMAGE_BILLING_ENFORCE", "1")
    session = db_layer.session_factory()()
    result = ledger.record(session, request_id="req-1", estimated=None,
                           idempotency_key="img-2", purpose="studio_image")
    assert result["ok"] is False
    assert result["error"] == "BILLING_NOT_CONFIGURED"
    assert result["enforced"] is True
    session.close()


def test_a_configured_budget_is_reserved_normally(monkeypatch):
    _configured(monkeypatch)
    session = db_layer.session_factory()()
    result = ledger.record(session, request_id="req-1", estimated=1.0,
                           idempotency_key="img-3", purpose="studio_image")
    assert result["ok"] is True and result["priced"] is True
    assert result["state"] == ledger.RESERVED
    session.close()


def test_an_observation_can_carry_the_measured_cost_later(monkeypatch):
    session = db_layer.session_factory()()
    result = ledger.record(session, request_id="req-1", estimated=None,
                           idempotency_key="img-4")
    updated = ledger.mark_unpriced(session, result["reservation_id"], 0.42, note="实测")
    assert updated["state"] == ledger.UNPRICED
    assert updated["actual"] == 0.42
    assert ledger.summary(session, "req-1")["by_state"] == {ledger.UNPRICED: 1}
    session.close()


def test_unpriced_spend_never_claims_to_be_accounted(monkeypatch):
    """It must not inflate `committed`: an observation has no authorised amount."""
    _configured(monkeypatch, per_request="5")
    session = db_layer.session_factory()()
    ledger.record(session, request_id="req-1", estimated=None, idempotency_key="img-5")
    before = ledger.summary(session, "req-1")["committed"]
    ledger.record(session, request_id="req-1", estimated=None, idempotency_key="img-6")
    assert ledger.summary(session, "req-1")["committed"] == before == 0.0
    assert ledger.summary(session, "req-1")["by_state"][ledger.UNPRICED] == 2
    session.close()
