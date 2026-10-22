"""Durable leases: exclusivity, expiry-based recovery, and bounded attempts."""
from datetime import datetime, timedelta, timezone

import pytest

from app import db as db_layer
from app.runtime import lease


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'lease.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("LG_LEASE_TTL_SECONDS", "90")
    monkeypatch.setenv("LG_RUN_MAX_ATTEMPTS", "2")
    db_layer.reset_for_tests(url)
    db_layer.init_db()
    yield
    db_layer.reset_for_tests(url)


def _db():
    return db_layer.session_factory()()


def _expire(run_id):
    """Force a lease past its expiry, as a stopped worker would leave it."""
    session = _db()
    session.execute(lease.models.AgentRunLease.__table__.update()
                    .where(lease.models.AgentRunLease.run_id == run_id)
                    .values(expires_at=datetime.now(timezone.utc) - timedelta(seconds=5)))
    session.commit()
    session.close()


def test_a_new_run_is_claimed_once():
    db = _db()
    first = lease.claim(db, run_id="r1", conversation_id="c1", owner="w1")
    db.commit()
    assert first["claimed"] is True and first["attempt"] == 1
    again = lease.claim(db, run_id="r1", conversation_id="c1", owner="w1")
    assert again["claimed"] is True and again["reason"] == "already_owned"
    db.close()


def test_another_worker_cannot_take_a_live_lease():
    db = _db()
    lease.claim(db, run_id="r1", conversation_id="c1", owner="w1")
    db.commit()
    other = lease.claim(db, run_id="r1", conversation_id="c1", owner="w2")
    assert other["claimed"] is False
    assert other["reason"] == "held_by_another_worker"
    assert other["owner"] == "w1"
    db.close()


def test_an_expired_lease_is_reclaimed_and_counts_as_an_attempt():
    db = _db()
    lease.claim(db, run_id="r1", conversation_id="c1", owner="w1")
    db.commit()
    _expire("r1")
    recovered = lease.claim(db, run_id="r1", conversation_id="c1", owner="w2")
    db.commit()
    assert recovered["claimed"] is True
    assert recovered["reason"] == "reclaimed_expired"
    assert recovered["attempt"] == 2
    db.close()


def test_attempts_are_bounded():
    """A run that keeps dying must end, not be retried forever."""
    db = _db()
    lease.claim(db, run_id="r1", conversation_id="c1", owner="w1")
    db.commit()
    _expire("r1")
    lease.claim(db, run_id="r1", conversation_id="c1", owner="w2")   # attempt 2
    db.commit()
    _expire("r1")
    third = lease.claim(db, run_id="r1", conversation_id="c1", owner="w3")
    assert third["claimed"] is False
    assert third["reason"] == "attempts_exhausted"
    assert third["max_attempts"] == 2
    db.close()


def test_a_released_lease_is_not_reported_as_abandoned():
    db = _db()
    lease.claim(db, run_id="r1", conversation_id="c1", owner="w1")
    db.commit()
    assert lease.release(db, run_id="r1", owner="w1") is True
    db.commit()
    assert lease.abandoned(db) == []
    info = lease.describe(db, "r1")
    assert info["state"] == "released" and info["expired"] is False
    db.close()


def test_an_expired_lease_is_reported_as_abandoned():
    db = _db()
    lease.claim(db, run_id="r1", conversation_id="c1", owner="w1")
    db.commit()
    _expire("r1")
    assert lease.abandoned(db) == ["r1"]
    assert lease.describe(db, "r1")["expired"] is True
    db.close()


def test_heartbeat_extends_only_the_owners_lease():
    db = _db()
    lease.claim(db, run_id="r1", conversation_id="c1", owner="w1", ttl=30)
    db.commit()
    before = lease.describe(db, "r1")["expires_at"]
    assert lease.heartbeat(db, run_id="r1", owner="w2", ttl=600) is False
    assert lease.heartbeat(db, run_id="r1", owner="w1", ttl=600) is True
    db.commit()
    assert lease.describe(db, "r1")["expires_at"] > before
    db.close()


def test_release_by_a_non_owner_is_refused():
    db = _db()
    lease.claim(db, run_id="r1", conversation_id="c1", owner="w1")
    db.commit()
    assert lease.release(db, run_id="r1", owner="w2") is False
    assert lease.describe(db, "r1")["state"] == "held"
    db.close()


def test_reclaim_is_atomic_under_concurrency():
    """Two workers racing to recover one abandoned run: exactly one must win."""
    import threading

    db = _db()
    lease.claim(db, run_id="r1", conversation_id="c1", owner="w0")
    db.commit()
    db.close()
    _expire("r1")

    outcomes = []
    barrier = threading.Barrier(2)

    def worker(name):
        session = _db()
        try:
            barrier.wait()
            outcomes.append(lease.claim(session, run_id="r1", conversation_id="c1",
                                        owner=name))
            session.commit()
        finally:
            session.close()

    threads = [threading.Thread(target=worker, args=(f"w{index}",)) for index in (1, 2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    claimed = [row for row in outcomes if row["claimed"]]
    assert len(claimed) == 1, f"exactly one worker must reclaim it: {outcomes}"
    assert any(row["reason"] in ("reclaim_race_lost", "held_by_another_worker")
               for row in outcomes if not row["claimed"])


def test_worker_id_is_stable_within_a_process():
    assert lease.worker_id() == lease.worker_id()
    assert str(__import__("os").getpid()) in lease.worker_id()

def test_orphan_recovery_leaves_a_live_lease_alone():
    """A run another live worker is still beating on is not an orphan."""
    from app.runtime import runner

    db = _db()
    conversation = lease.models.AgentConversation(workspace_id="default",
                                                  runner_version="langgraph_autonomous_v1")
    db.add(conversation)
    db.flush()
    run = lease.models.AgentRun(conversation_id=conversation.id, status="running")
    db.add(run)
    db.flush()
    lease.claim(db, run_id=run.id, conversation_id=conversation.id, owner="other-worker",
                ttl=600)
    db.commit()

    assert runner.recover_orphaned_runs(db) == 0
    db.commit()
    assert db.get(lease.models.AgentRun, run.id).status == "running"
    db.close()


def test_orphan_recovery_recovers_an_expired_lease():
    from app.runtime import runner

    db = _db()
    conversation = lease.models.AgentConversation(workspace_id="default",
                                                  runner_version="langgraph_autonomous_v1")
    db.add(conversation)
    db.flush()
    run = lease.models.AgentRun(conversation_id=conversation.id, status="running")
    db.add(run)
    db.flush()
    lease.claim(db, run_id=run.id, conversation_id=conversation.id, owner="dead-worker")
    db.commit()
    _expire(run.id)

    assert runner.recover_orphaned_runs(db) == 1
    db.commit()
    row = db.get(lease.models.AgentRun, run.id)
    assert row.status == "needs_reconciliation"
    assert row.stop_reason == "orphaned_by_restart"
    db.close()


def test_orphan_recovery_still_handles_runs_without_a_lease():
    """Pre-lease rows have no lease at all and must still be recovered, not ignored."""
    from app.runtime import runner

    db = _db()
    conversation = lease.models.AgentConversation(workspace_id="default",
                                                  runner_version="langgraph_autonomous_v1")
    db.add(conversation)
    db.flush()
    run = lease.models.AgentRun(conversation_id=conversation.id, status="running")
    db.add(run)
    db.commit()

    assert runner.recover_orphaned_runs(db) == 1
    db.commit()
    assert db.get(lease.models.AgentRun, run.id).status == "needs_reconciliation"
    db.close()


def test_startup_reclaim_releases_leases_from_a_previous_process():
    """Single worker: a lease we do not own at startup belongs to a process that is gone."""
    db = _db()
    lease.claim(db, run_id="r1", conversation_id="c1", owner="previous-process", ttl=600)
    db.commit()
    assert lease.describe(db, "r1")["expired"] is False

    released = lease.reclaim_stale_on_start(db, assume_single_worker=True)
    db.commit()
    assert released == 1
    info = lease.describe(db, "r1")
    assert info["state"] == "released"
    assert info["reason"] == "previous_process_gone"
    db.close()


def test_startup_reclaim_is_skipped_for_a_multi_worker_deployment():
    """With more than one worker a live lease may belong to a healthy peer."""
    db = _db()
    lease.claim(db, run_id="r1", conversation_id="c1", owner="healthy-peer", ttl=600)
    db.commit()
    assert lease.reclaim_stale_on_start(db, assume_single_worker=False) == 0
    db.commit()
    assert lease.describe(db, "r1")["state"] == "held"
    db.close()


def test_startup_reclaim_never_touches_our_own_lease():
    db = _db()
    lease.claim(db, run_id="r1", conversation_id="c1")  # owner is this process
    db.commit()
    assert lease.reclaim_stale_on_start(db, assume_single_worker=True) == 0
    db.commit()
    assert lease.describe(db, "r1")["state"] == "held"
    db.close()
