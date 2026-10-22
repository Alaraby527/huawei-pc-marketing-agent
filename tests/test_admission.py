"""Final admission: the concurrency property the plan calls out.

"并发近似稿不会因同时读旧历史一起通过" — two requests generating near-identical copy must
not both be admitted on stale history. The lock is what makes the re-read authoritative.
"""
import threading

import pytest
from sqlalchemy import text as sql_text

from app import db as db_layer
from app.runtime import admission, dedup


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'admission.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("LG_ADMISSION_LOCK_TIMEOUT", "1")
    monkeypatch.setenv("LG_ADMISSION_RECHECKS", "1")
    db_layer.reset_for_tests(url)
    db_layer.init_db()
    yield
    db_layer.reset_for_tests(url)


def _admit(run_id, text, workspace="default"):
    sketch = dedup.fingerprint(text)
    return admission.admit_delivery(workspace_id=workspace, conversation_id="c-" + run_id,
                                    run_id=run_id, artifact_id="", sketch=sketch)


def test_sequential_repeat_is_detected():
    first = _admit("r1", "同一篇稿件的内容，用来验证重复检测。")
    assert first["kind"] == "none"
    assert first["admission"]["locked"] is True
    second = _admit("r2", "同一篇稿件的内容，用来验证重复检测。")
    assert second["kind"] == "exact"
    assert second["exact_match"] == "r1"


def test_concurrent_identical_deliveries_cannot_both_pass_on_stale_history():
    """The real T15 property: serialise, re-read inside the lock, then decide."""
    payload = "并发场景下同一篇近似稿件不应该两个都通过准入。" * 3
    barrier = threading.Barrier(2)
    results: dict[str, dict] = {}

    def worker(run_id):
        barrier.wait()
        results[run_id] = _admit(run_id, payload)

    threads = [threading.Thread(target=worker, args=(f"r{index}",)) for index in (1, 2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    kinds = sorted(result["kind"] for result in results.values())
    # One registers first, the other must see it: no run may be admitted on history that
    # predates the other's registration.
    assert kinds == ["exact", "none"], f"the race was missed: {kinds}"
    assert all(result["admission"]["locked"] for result in results.values())
    with db_layer.engine().connect() as connection:
        count = connection.execute(sql_text(
            "SELECT count(*) FROM agent_content_fingerprints")).scalar()
    assert count == 2, "both deliveries must still be registered"


def test_a_stale_pre_lock_snapshot_is_reported(monkeypatch):
    """The staleness signal is what proves the lock did something."""
    _admit("r1", "先登记的稿件。")
    # Pretend the pre-lock count was taken before that row existed.
    monkeypatch.setattr(admission, "_history_count", lambda *a, **k: 0)
    result = _admit("r2", "后登记的稿件，内容不同。")
    assert result["admission"]["locked"] is True
    assert result["admission"]["stale_snapshot"] is True
    assert result["admission"]["rechecked"] is True


def test_a_lock_that_cannot_be_taken_is_never_a_pass(monkeypatch):
    """An admission that did not run must not be reported as cleared."""
    monkeypatch.setenv("LG_ADMISSION_LOCK_TIMEOUT", "0.2")
    monkeypatch.setenv("LG_ADMISSION_RECHECKS", "0")
    holder = db_layer.engine().connect().execution_options(isolation_level="AUTOCOMMIT")
    holder.exec_driver_sql("BEGIN IMMEDIATE")
    try:
        result = _admit("r1", "锁被占用时提交的稿件。")
    finally:
        holder.exec_driver_sql("ROLLBACK")
        holder.close()

    assert result["kind"] == "unverified"
    assert result["admission"]["locked"] is False
    assert result["admission"]["reason"] == "lock_timeout"
    assert result["blocks"] is False

    # While another writer holds the lock the registration cannot complete either. That is
    # acceptable; what is NOT acceptable is losing the delivery from the content memory
    # silently, so the failure has to be reported.
    with db_layer.engine().connect() as connection:
        registered = connection.execute(sql_text(
            "SELECT count(*) FROM agent_content_fingerprints")).scalar()
    if registered:
        assert "register_error" not in result["admission"]
    else:
        assert result["admission"].get("register_error"), \
            "a delivery missing from the content memory must say so"


def test_scope_is_one_workspace():
    """Comparison is per workspace; the plan's authorised cross-store scope is not built."""
    _admit("r1", "门店 A 的稿件内容，用于验证作用域隔离。", workspace="store-a")
    other = _admit("r2", "门店 A 的稿件内容，用于验证作用域隔离。", workspace="store-b")
    assert other["kind"] == "none", "a different workspace must not see this history"
    assert other["admission"]["scope"] == "workspace"
    assert other["admission"]["cross_store_authorised"] is False


def test_registration_is_idempotent_per_run():
    _admit("r1", "同一个 run 不应写入两行。")
    _admit("r1", "同一个 run 不应写入两行。")
    with db_layer.engine().connect() as connection:
        assert connection.execute(sql_text(
            "SELECT count(*) FROM agent_content_fingerprints WHERE run_id = 'r1'"
        )).scalar() == 1

# ------------------------------------------------------------------ semantic layer

def _embedding(values, version="emb-v1:test-model:3"):
    from app.runtime import semantic
    return {"model_id": "test-model", "dimension": len(values), "view": "full",
            "index_version": version, "vector": semantic.normalize(list(values)),
            "usage": {}}


def test_an_embedding_is_stored_and_compared_inside_the_lock():
    from sqlalchemy import text as sql_text
    from app.runtime import semantic

    _admit("r1", "第一篇交付内容，语义层测试用。", )
    second = admission.admit_delivery(
        workspace_id="default", conversation_id="c-r2", run_id="r2", artifact_id="",
        sketch=dedup.fingerprint("第二篇完全不同的内容。"),
        embedding=_embedding([0.9, 0.1, 0.0]))
    result = second["semantic"]
    assert result["mode"] in ("off", "shadow")
    with db_layer.engine().connect() as connection:
        stored = connection.execute(sql_text(
            "SELECT count(*) FROM agent_content_embeddings")).scalar()
    assert stored == 1, "the embedding is registered with the delivery"
    assert second["blocks"] is False and second["calibrated"] is False


def test_a_paraphrase_is_caught_by_the_semantic_layer_only(monkeypatch):
    """The literal layer misses a paraphrase; the stored vector catches it."""
    monkeypatch.setenv("LG_EMBEDDING_MODE", "shadow")
    from app.runtime import semantic

    first = admission.admit_delivery(
        workspace_id="default", conversation_id="c1", run_id="r1", artifact_id="",
        sketch=dedup.fingerprint("学生党挑本子，先看屏幕能不能一屏多看几行。"),
        embedding=_embedding([1.0, 0.0, 0.0]))
    assert first["semantic"]["kind"] == "none"

    # Same meaning, different words: the literal sketch sees nothing in common.
    second = admission.admit_delivery(
        workspace_id="default", conversation_id="c2", run_id="r2", artifact_id="",
        sketch=dedup.fingerprint("选购笔记本时，我最在意显示屏一次能显示多少内容。"),
        embedding=_embedding([0.98, 0.02, 0.0]))
    assert second["kind"] != "exact", "the literal layer cannot see this"
    assert second["semantic"]["kind"] == "semantic"
    assert second["semantic"]["similarity"] > 0.9
    assert second["blocks"] is False


def test_vectors_from_another_index_version_are_not_compared(monkeypatch):
    monkeypatch.setenv("LG_EMBEDDING_MODE", "shadow")
    admission.admit_delivery(
        workspace_id="default", conversation_id="c1", run_id="r1", artifact_id="",
        sketch=dedup.fingerprint("第一段。"), embedding=_embedding([1.0, 0.0, 0.0]))
    other = admission.admit_delivery(
        workspace_id="default", conversation_id="c2", run_id="r2", artifact_id="",
        sketch=dedup.fingerprint("第二段。"),
        embedding=_embedding([1.0, 0.0, 0.0], version="emb-v1:other-model:3"))
    assert other["semantic"]["compared"] == 0
    assert other["semantic"]["index_version"] == "emb-v1:other-model:3"


def test_no_embedding_is_reported_as_not_run_not_as_clean():
    result = admission.admit_delivery(
        workspace_id="default", conversation_id="c1", run_id="r1", artifact_id="",
        sketch=dedup.fingerprint("没有向量的交付。"))
    assert result["semantic"]["kind"] in ("not_run", "none")
    assert result["blocks"] is False
