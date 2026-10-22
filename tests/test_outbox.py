"""Outbox: same-transaction registration, bounded retries, and no silent loss."""
import pytest
from sqlalchemy import text as sql_text

from app import db as db_layer
from app.runtime import admission, dedup, outbox


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'outbox.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("LG_OUTBOX_MAX_ATTEMPTS", "2")
    monkeypatch.setenv("LG_ADMISSION_LOCK_TIMEOUT", "1")
    db_layer.reset_for_tests(url)
    db_layer.init_db()
    yield
    db_layer.reset_for_tests(url)


def _admit(run_id="r1", text="一篇交付内容。"):
    return admission.admit_delivery(
        workspace_id="default", conversation_id="c-" + run_id, run_id=run_id,
        artifact_id="a1", sketch=dedup.fingerprint(text))


def test_registration_and_handoff_commit_together():
    """The plan requires one transaction: no delivery without its handoff record."""
    _admit()
    with db_layer.engine().connect() as connection:
        fingerprints = connection.execute(sql_text(
            "SELECT count(*) FROM agent_content_fingerprints")).scalar()
        events = connection.execute(sql_text(
            "SELECT count(*) FROM agent_outbox_events")).scalar()
    assert fingerprints == 1 and events == 1


def test_the_event_carries_enough_to_act_on():
    _admit()
    session = db_layer.session_factory()()
    event = outbox.pending(session)[0]
    assert event.event_type == outbox.CONTENT_REGISTERED
    assert event.run_id == "r1"
    assert event.payload.get("artifact_id") == "a1"
    assert event.payload.get("blocks") is False
    session.close()


def test_drain_delivers_and_records_it():
    _admit()
    session = db_layer.session_factory()()
    counters = outbox.drain(session)
    session.commit()
    assert counters["delivered"] == 1
    assert outbox.stats(session)["by_state"] == {"delivered": 1}
    session.close()


def test_a_failing_consumer_retries_then_fails_visibly():
    """A consumer being down must delay the event, never discard it quietly."""
    _admit()
    session = db_layer.session_factory()()
    attempts = {"n": 0}

    def broken(_event):
        attempts["n"] += 1
        raise RuntimeError("consumer down")

    first = outbox.drain(session, handler=broken)
    session.commit()
    assert first["retried"] == 1 and first["failed"] == 0
    assert outbox.stats(session)["by_state"] == {"pending": 1}

    second = outbox.drain(session, handler=broken)
    session.commit()
    assert second["failed"] == 1
    assert outbox.stats(session)["by_state"] == {"failed": 1}
    event = session.query(outbox.models.AgentOutboxEvent).first()
    assert "consumer down" in event.last_error
    assert event.attempts == 2
    session.close()


def test_a_recovered_consumer_delivers_a_pending_event():
    _admit()
    session = db_layer.session_factory()()
    outbox.drain(session, handler=lambda _event: (_ for _ in ()).throw(RuntimeError("x")))
    session.commit()
    assert outbox.stats(session)["by_state"] == {"pending": 1}
    assert outbox.drain(session)["delivered"] == 1
    session.commit()
    assert outbox.stats(session)["by_state"] == {"delivered": 1}
    session.close()


def test_no_events_are_created_for_a_delivery_that_was_not_registered():
    """A lock timeout registers nothing, so it must not emit a handoff record either."""
    holder = db_layer.engine().connect().execution_options(isolation_level="AUTOCOMMIT")
    holder.exec_driver_sql("BEGIN IMMEDIATE")
    original = admission.lock_timeout_seconds
    admission.lock_timeout_seconds = lambda: 0.2
    try:
        result = _admit("r-locked")
    finally:
        admission.lock_timeout_seconds = original
        holder.exec_driver_sql("ROLLBACK")
        holder.close()
    assert result["admission"]["locked"] is False
    session = db_layer.session_factory()()
    assert outbox.stats(session)["total"] == 0
    session.close()
