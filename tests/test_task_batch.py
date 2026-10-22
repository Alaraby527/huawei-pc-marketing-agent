"""Task management endpoints: batch deletion reports every id, not just a total."""
import pytest
from fastapi.testclient import TestClient

from app import db as db_layer, models
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'tasks.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    db_layer.reset_for_tests(url)
    db_layer.init_db()
    yield
    db_layer.reset_for_tests(url)


def _make(count=3):
    ids = []
    for _ in range(count):
        response = client.post("/api/v2/conversations", json={})
        assert response.status_code in (200, 201), response.text
        ids.append(response.json()["conversation_id"])
    return ids


def test_batch_delete_removes_every_selected_task():
    ids = _make(3)
    response = client.post("/api/v2/conversations/batch-delete", json={"ids": ids})
    assert response.status_code == 200
    data = response.json()
    assert data["deleted_count"] == 3
    assert sorted(data["deleted"]) == sorted(ids)
    session = db_layer.session_factory()()
    assert session.query(models.AgentConversation).count() == 0
    session.close()


def test_a_missing_id_is_reported_and_does_not_abort_the_rest():
    """One stale id must not cost the other deletions."""
    ids = _make(2)
    response = client.post("/api/v2/conversations/batch-delete",
                           json={"ids": ids + ["does-not-exist-0001"]})
    data = response.json()
    assert data["deleted_count"] == 2
    assert data["missing"] == ["does-not-exist-0001"]
    assert data["failed"] == []
    assert data["requested_count"] == 3


def test_the_response_separates_deleted_missing_and_failed():
    """A bare total would hide a partial failure."""
    data = client.post("/api/v2/conversations/batch-delete",
                       json={"ids": _make(1) + ["nope-1"]}).json()
    for key in ("deleted", "missing", "failed", "deleted_count", "requested_count"):
        assert key in data, key
    assert "不合并成一句成功" in data["note"]


def test_duplicate_ids_are_counted_once():
    ids = _make(1)
    data = client.post("/api/v2/conversations/batch-delete",
                       json={"ids": ids + ids}).json()
    assert data["requested_count"] == 1
    assert data["deleted_count"] == 1


def test_an_empty_selection_is_refused():
    response = client.post("/api/v2/conversations/batch-delete", json={"ids": []})
    assert response.status_code == 400
    assert response.json()["code"] == "NO_IDS"


def test_an_oversized_batch_is_refused():
    response = client.post("/api/v2/conversations/batch-delete",
                           json={"ids": [f"id-{index}" for index in range(101)]})
    assert response.status_code == 400
    assert response.json()["code"] == "TOO_MANY_IDS"


def test_deleting_a_task_leaves_no_rows_behind():
    """Deleting only the business rows would leave a resumable orphan checkpoint.

    The purge itself is internal, so this checks the observable end state: after a batch delete
    the conversation, its task state and its messages are all gone.
    """
    from sqlalchemy import text
    ids = _make(1)
    session = db_layer.session_factory()()
    session.add(models.AgentTaskState(conversation_id=ids[0], stage="intake",
                                      data={"note": "should be removed"}))
    session.add(models.AgentMessage(conversation_id=ids[0], seq=1, role="user",
                                    content="hi"))
    session.commit()
    data = client.post("/api/v2/conversations/batch-delete", json={"ids": ids}).json()
    assert data["deleted_count"] == 1
    for table in ("agent_conversations", "agent_task_states", "agent_messages"):
        left = session.execute(text("SELECT count(*) FROM " + table + " WHERE "
                                    + ("id" if table == "agent_conversations"
                                       else "conversation_id") + " = :cid"),
                               {"cid": ids[0]}).scalar()
        assert left == 0, table + " 仍有残留行"
    session.close()


def test_the_single_delete_endpoint_still_reports_a_missing_task_as_404():
    """The batch is lenient by design; the single endpoint keeps its 404."""
    response = client.delete("/api/v2/conversations/does-not-exist-0002")
    assert response.status_code == 404