import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app import agent_runtime, db as db_layer
from app.contracts import Fact, Source
from app.main import app

client = TestClient(app)


def _fact(fid, text):
    return Fact(fact_id=fid, product_id="P-test", text=text, conditions="",
                source=Source(kind="local_official_export", title="合成测试材料",
                              locator="合成测试材料/规格", excerpt="合成测试"))


class FakeProduct:
    model = "HUAWEI MateBook Test"
    product_id = "P-test"
    issues = []
    docs = []
    facts = [_fact("F-MEM", "内存：16 GB"), _fact("F-STO", "存储：512 GB"),
             _fact("F-WEIGHT", "尺寸与重量：约 1.33 kg")]


class FakeCatalog:
    products = [FakeProduct()]
    entries = []
    image_index = []


@pytest.fixture(autouse=True)
def runtime_env(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'runtime.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    db_layer.reset_for_tests(url)

    async def fake_catalog(*args, **kwargs):
        return FakeCatalog()

    monkeypatch.setattr("app.agent_runtime.load_catalog", fake_catalog)
    yield


def _script(monkeypatch, results):
    calls = []

    def fake(system, messages, tools, model=None):
        calls.append(messages)
        item = results.pop(0)
        return item, {"provider": "stub", "model": model or "stub",
                      "usage": {"prompt_tokens": 10, "completion_tokens": 5}}

    monkeypatch.setattr("app.agent_runtime.chat_tools", fake)
    return calls


def _call(name, args, call_id="c1"):
    return {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}


def _conversation():
    return client.post("/api/v2/conversations", json={}).json()["conversation_id"]


def test_tool_roundtrip_is_persisted(monkeypatch):
    _script(monkeypatch, [
        {"content": "", "tool_calls": [_call("search_products", {"query": "MateBook"})]},
        {"content": "找到了 1 个候选，请确认是否继续。", "tool_calls": []},
    ])
    conversation_id = _conversation()
    data = client.post(f"/api/v2/conversations/{conversation_id}/messages",
                       json={"message": "帮我看看 MateBook"}).json()
    assert data["status"] == "completed"
    assert "候选" in data["reply"]
    assert any(event["type"] == "tool_finished" and event["tool"] == "search_products"
               for event in data["events"])
    conversation = client.get(f"/api/v2/conversations/{conversation_id}").json()
    roles = [message["role"] for message in conversation["messages"]]
    assert "tool" in roles and "assistant" in roles


def test_ask_user_pauses_and_resumes(monkeypatch):
    _script(monkeypatch, [{"content": "", "tool_calls": [
        _call("ask_user", {"question": "内容面向谁？", "options": ["大学新生", "职场办公"]})]}])
    conversation_id = _conversation()
    first = client.post(f"/api/v2/conversations/{conversation_id}/messages",
                        json={"message": "帮我写点内容"}).json()
    assert first["status"] == "waiting_user"
    assert first["reply"] == "内容面向谁？"
    _script(monkeypatch, [{"content": "好的，面向大学新生来写。", "tool_calls": []}])
    second = client.post(f"/api/v2/conversations/{conversation_id}/messages",
                         json={"message": "大学新生，你定就行"}).json()
    assert second["status"] == "completed"
    conversation = client.get(f"/api/v2/conversations/{conversation_id}").json()
    assert [m["role"] for m in conversation["messages"]].count("user") == 2


def test_wrong_parameter_is_rejected_and_not_saved(monkeypatch):
    _script(monkeypatch, [
        {"content": "", "tool_calls": [_call("read_product_evidence", {"product_id": "P-test"})]},
        {"content": "", "tool_calls": [_call("save_artifact", {
            "artifact_type": "post", "product_id": "P-test",
            "content": {"product_id": "P-test", "title": "这台机器有 32 GB 内存",
                        "body": "适合大学新生。"}}, "c2")]},
        {"content": "32 GB 与知识库不符，已改为 16 GB。", "tool_calls": []},
    ])
    conversation_id = _conversation()
    data = client.post(f"/api/v2/conversations/{conversation_id}/messages",
                       json={"message": "写一篇，内存写 32GB"}).json()
    assert data["status"] == "completed"
    assert any(event.get("tool") == "save_artifact" and event.get("ok") is False
               for event in data["events"])
    conversation = client.get(f"/api/v2/conversations/{conversation_id}").json()
    assert conversation["artifacts"] == []


def _ctx():
    session = db_layer.session_factory()()
    conversation = agent_runtime.models.AgentConversation(workspace_id="default")
    session.add(conversation)
    session.flush()
    run = agent_runtime.models.AgentRun(conversation_id=conversation.id, status="running")
    session.add(run)
    session.flush()
    return session, agent_runtime.ToolContext(db=session, conversation=conversation, run=run,
                                              catalog=FakeCatalog())


def test_local_patch_keeps_other_pages_and_bumps_revision():
    session, ctx = _ctx()
    content = {"product_id": "P-test", "title": "校园选本",
               "pages": [{"role": "hook", "title": "第一页", "text": "原文一"},
                         {"role": "scene", "title": "第二页", "text": "原文二"},
                         {"role": "close", "title": "第三页", "text": "原文三"}]}
    first = agent_runtime._save_artifact(ctx, {"artifact_type": "pages", "product_id": "P-test",
                                               "content": content})
    assert first["ok"] is True
    second = agent_runtime._save_artifact(ctx, {
        "artifact_id": first["artifact_id"], "base_revision": 1,
        "patch": {"pages.1.text": "改得更自然"}})
    assert second["ok"] is True and second["revision"] == 2
    loaded = agent_runtime._load_artifact(ctx, {"artifact_id": first["artifact_id"]})
    pages = loaded["content"]["pages"]
    assert pages[0]["text"] == "原文一" and pages[2]["text"] == "原文三"
    assert pages[1]["text"] == "改得更自然"
    session.close()


def test_render_supports_variable_page_count():
    session, ctx = _ctx()
    content = {"product_id": "P-test", "title": "三页方案",
               "pages": [{"role": "hook", "title": "封面", "text": "开场"},
                         {"role": "proof", "title": "事实", "text": "内存 16 GB"},
                         {"role": "close", "title": "收尾", "text": "到店体验"}]}
    first = agent_runtime._save_artifact(ctx, {"artifact_type": "pages", "product_id": "P-test",
                                               "content": content})
    rendered = asyncio.run(agent_runtime._render(ctx, {"artifact_id": first["artifact_id"]}))
    assert rendered["ok"] is True and rendered["page_count"] == 3
    assert all(url.startswith("/api/v1/assets/") for url in rendered["images"])
    session.close()


def test_v2_requires_database(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    response = client.post("/api/v2/conversations", json={})
    assert response.status_code == 503
