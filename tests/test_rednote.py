import io
import zipfile

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import db as db_layer
from app.main import app

client = TestClient(app)
PREFIX = "/api/v1/workspaces/default/apps/rednote"


@pytest.fixture(autouse=True)
def workspace_db(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'rednote.db'}"
    monkeypatch.setenv("REDNOTE_DATA_DIR", str(tmp_path / "data"))
    db_layer.reset_for_tests(url)
    yield
    monkeypatch.delenv("DATABASE_URL", raising=False)


def test_capabilities_reports_pending_integrations():
    data = client.get(f"{PREFIX}/capabilities").json()
    assert data["database"] == "ready"
    assert data["connector"]["write"] == "pending_manual_verification"
    assert data["auth"]["multi_tenant"] is False


def test_workspace_header_mismatch_is_rejected():
    response = client.get(f"{PREFIX}/capabilities", headers={"X-Workspace-Id": "other"})
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "WORKSPACE_ACCESS_DENIED"


def test_workspace_requires_database(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    response = client.get(f"{PREFIX}/capabilities")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "CAPABILITY_DISABLED"


def _create_item():
    response = client.post(f"{PREFIX}/brief-handoffs/consume", json={
        "product_model": "HUAWEI MateBook 14", "product_configuration": "型号级",
        "objective": "到店体验", "account_ref": "demo-account",
    })
    assert response.status_code == 200, response.text
    return response.json()["content_item_id"]


def test_m1_main_chain_from_brief_to_manual_publish(tmp_path):
    item_id = _create_item()

    selection = client.post(f"{PREFIX}/trend-selections", json={
        "content_item_id": item_id,
        "main_topic": {"title": "开学季：学习、携带与续航怎么平衡", "source": "editorial-fallback"},
        "aux_topics": [],
        "scenario": "课堂学习",
        "benefits": ["轻便"],
        "evidence_ids": ["editorial-1"],
    })
    assert selection.status_code == 200, selection.text

    directions = client.post(f"{PREFIX}/creative-directions",
                             json={"content_item_id": item_id, "count": 6}).json()
    assert len(directions["directions"]) == 6
    assert directions["diversity"]["meets_target"] is True

    picked = [row["id"] for row in directions["directions"][:3]]
    board = client.post(f"{PREFIX}/creative-boards",
                        json={"content_item_id": item_id, "direction_ids": picked}).json()
    preview = client.post(f"{PREFIX}/creative-boards/{board['board_id']}/preview").json()
    assert len(preview["candidates"]) == 6
    asset = client.get(preview["candidates"][0]["asset_url"])
    assert asset.status_code == 200
    assert asset.content.startswith(b"\x89PNG")

    document = client.post(f"{PREFIX}/design-documents", json={
        "content_item_id": item_id, "engine": "vue-fabric-editor", "engine_version": "test",
        "native_payload": {"objects": [{"type": "text", "text": "封面"}]},
        "sidecar": {"bindings": []}, "manifest": {"assets": [], "fonts": ["Noto Sans SC"]},
    }).json()
    stale = client.patch(f"{PREFIX}/design-documents/{document['id']}",
                         json={"expected_version": 99, "native_payload": {}})
    assert stale.status_code == 409 and stale.json()["code"] == "STALE_REVISION"
    updated = client.patch(f"{PREFIX}/design-documents/{document['id']}",
                           json={"expected_version": 1,
                                 "native_payload": {"objects": [{"type": "text", "text": "封面 v2"}]}}).json()
    assert updated["version"] == 2 and updated["revision_hash"] != document["revision_hash"]

    revision = client.post(f"{PREFIX}/review-submissions", json={
        "content_item_id": item_id, "design_document_id": document["id"]}).json()
    approval = client.post(f"{PREFIX}/approvals", json={
        "content_item_id": item_id, "revision_id": revision["revision_id"],
        "account_ref": "demo-account", "approved_window": "2026-10-20T09:00/2026-10-20T12:00",
        "approved_by": "tester"}).json()
    assert approval["status"] == "approved"

    plan = client.post(f"{PREFIX}/publish-plans", json={
        "content_item_id": item_id, "revision_id": revision["revision_id"],
        "account_ref": "demo-account", "mode": "manual"}).json()
    attempt = client.post(f"{PREFIX}/publish-plans/{plan['plan_id']}/execute").json()
    assert attempt["status"] == "WAITING_MANUAL"

    result = client.post(f"{PREFIX}/publish-plans/{plan['plan_id']}/manual-results", json={
        "url": "https://www.xiaohongshu.com/explore/demo", "platform_note_id": "demo"}).json()
    assert result["visibility_status"] == "unknown"
    assert "待核对" in result["message"]

    overview = client.get(f"{PREFIX}/analytics/overview").json()
    assert overview["data_coverage"]["creator_metrics"] == "not_connected"
    assert client.get(f"{PREFIX}/comments").json()["comments"] == []
    assert client.get(f"{PREFIX}/alerts").json()["alerts"] == []


def test_connector_publish_is_pending_not_faked():
    item_id = _create_item()
    document = client.post(f"{PREFIX}/design-documents", json={
        "content_item_id": item_id, "native_payload": {}, "sidecar": {}, "manifest": {}}).json()
    revision = client.post(f"{PREFIX}/review-submissions", json={
        "content_item_id": item_id, "design_document_id": document["id"]}).json()
    client.post(f"{PREFIX}/approvals", json={
        "content_item_id": item_id, "revision_id": revision["revision_id"], "approved_by": "tester"})
    plan = client.post(f"{PREFIX}/publish-plans", json={
        "content_item_id": item_id, "revision_id": revision["revision_id"], "mode": "connector"}).json()
    response = client.post(f"{PREFIX}/publish-plans/{plan['plan_id']}/execute")
    assert response.status_code == 501
    assert response.json()["code"] == "CAPABILITY_DISABLED"


def test_publish_requires_approval():
    item_id = _create_item()
    document = client.post(f"{PREFIX}/design-documents", json={
        "content_item_id": item_id, "native_payload": {}, "sidecar": {}, "manifest": {}}).json()
    revision = client.post(f"{PREFIX}/review-submissions", json={
        "content_item_id": item_id, "design_document_id": document["id"]}).json()
    response = client.post(f"{PREFIX}/publish-plans", json={
        "content_item_id": item_id, "revision_id": revision["revision_id"], "mode": "manual"})
    assert response.status_code == 409
    assert response.json()["code"] == "APPROVAL_REQUIRED"


def test_cross_workspace_object_is_rejected():
    item_id = _create_item()
    response = client.post(f"{PREFIX}/trend-selections", json={
        "content_item_id": "not-a-real-item", "main_topic": {}})
    assert response.status_code == 404
    assert response.json()["code"] == "WORKSPACE_ACCESS_DENIED"


def test_agent_loop_executes_registered_tools(monkeypatch):
    item_id = _create_item()
    scripted = [
        {"thought": "先建方向", "tool": "create_directions",
         "args": {"content_item_id": item_id, "count": 6}, "reply": ""},
        {"thought": "完成", "tool": None, "args": {}, "reply": "已生成 6 个创意方向，请选择最多 3 个。"},
    ]

    def fake_complete(system, payload, schema_hint=None, model=None):
        return scripted.pop(0), {"provider": "stub"}

    monkeypatch.setattr('app.agent.complete_json', fake_complete)
    data = client.post(f"{PREFIX}/agent/turn", json={"message": "给我 6 个创意方向"}).json()
    assert data["engine"] == "agent"
    assert data["tool_trace"][0]["tool"] == "create_directions"
    assert data["tool_trace"][0]["ok"] is True
    assert "6" in data["reply"]


def test_agent_loop_reports_unknown_tool_without_crashing(monkeypatch):
    scripted = [
        {"tool": "publish_everything", "args": {}},
        {"tool": None, "args": {}, "reply": "该操作不存在。"},
    ]
    monkeypatch.setattr('app.agent.complete_json',
                        lambda *a, **k: (scripted.pop(0), {}))
    data = client.post(f"{PREFIX}/agent/turn", json={"message": "直接发布"}).json()
    assert data["tool_trace"][0]["ok"] is False
    assert "CAPABILITY_DISABLED" in data["tool_trace"][0]["summary"]


def test_agent_reports_unavailable_model(monkeypatch):
    from app.model import ModelError

    def boom(*args, **kwargs):
        raise ModelError("no key")

    monkeypatch.setattr('app.agent.complete_json', boom)
    data = client.post(f"{PREFIX}/agent/turn", json={"message": "开始"}).json()
    assert data["engine"] == "agent-unavailable"
    assert data["tool_trace"] == []
