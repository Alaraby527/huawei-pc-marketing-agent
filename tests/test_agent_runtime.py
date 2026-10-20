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


def test_mismatch_note_explains_unsupported_requested_value():
    facts = [{"fact_id": "F-MEM", "text": "内存：16 GB", "source": "合成测试材料",
              "conditions": ""}]
    note = agent_runtime._request_mismatch_note("内存写成 64GB", facts)
    assert "64GB" in note and "16 GB" in note
    assert "没有照这个数字改写" in note
    assert agent_runtime._request_mismatch_note("不要写成 64GB", facts) == ""
    explained = "知识库没有 64GB 版本，实际是 16 GB。"
    assert agent_runtime._request_mismatch_note("内存写成 64GB", facts, explained) == ""


def test_compose_reply_is_used_as_delivery_message(monkeypatch):
    session, ctx = _ctx()
    ctx.last_search = {
        "products": [{"product_id": "P-test"}],
        "evidence": [{"fact_id": "F-MEM", "text": "内存：16 GB",
                      "source": "合成测试材料", "conditions": ""}],
    }

    def fake(system, messages, schema_hint=None, model=None):
        return {"artifact_type": "post", "reply": "知识库没有 64 GB，这篇按 16 GB 写。",
                "content": {"product_id": "P-test", "title": "真实配置",
                            "body": "内存 16 GB"}}, {"model": "stub"}

    monkeypatch.setattr("app.agent_runtime.chat_json_messages", fake)
    saved, _ = agent_runtime._compose_after_evidence(ctx, "内存写成 64GB", None)
    assert saved["ok"] is True
    assert saved["reply"] == "知识库没有 64 GB，这篇按 16 GB 写。"
    session.close()


def test_requested_page_count_reads_digits_and_chinese():
    assert agent_runtime._requested_page_count("做 6 页图文") == 6
    assert agent_runtime._requested_page_count("做六页图文") == 6
    assert agent_runtime._requested_page_count("不要 6 页，改成 3 页") == 3
    assert agent_runtime._requested_page_count("随便来一篇") is None


def test_search_does_not_match_wrong_model_by_brand():
    session, ctx = _ctx()
    result = asyncio.run(agent_runtime._tool_search_products(
        ctx, {"query": "HUAWEI MateBook X Pro 2027"}))
    assert result["ok"] is True and result["count"] == 0
    known = asyncio.run(agent_runtime._tool_search_products(
        ctx, {"query": "HUAWEI MateBook Test"}))
    assert known["count"] == 1 and known["products"][0]["product_id"] == "P-test"
    session.close()


def test_compose_retries_when_page_count_is_wrong(monkeypatch):
    session, ctx = _ctx()
    ctx.last_search = {
        "products": [{"product_id": "P-test"}],
        "evidence": [{"fact_id": "F-MEM", "text": "内存：16 GB",
                      "source": "合成测试材料", "conditions": ""}],
    }
    responses = [
        {"artifact_type": "pages", "reply": "先给一页",
         "content": {"product_id": "P-test", "title": "草稿",
                     "pages": [{"role": "hook", "title": "第1页", "text": "内容一"}]}},
        {"artifact_type": "pages", "reply": "已按 3 页交付",
         "content": {"product_id": "P-test", "title": "成稿",
                     "pages": [{"role": "hook", "title": f"第{i}页", "text": f"内容{i}"}
                               for i in range(1, 4)]}},
    ]

    def fake(system, messages, schema_hint=None, model=None):
        return responses.pop(0), {"model": "stub",
                                  "usage": {"prompt_tokens": 10, "completion_tokens": 5}}

    monkeypatch.setattr("app.agent_runtime.chat_json_messages", fake)
    saved, meta = agent_runtime._compose_after_evidence(ctx, "做 3 页图文", None)
    assert saved["ok"] is True
    loaded = agent_runtime._load_artifact(ctx, {"artifact_id": saved["artifact_id"]})
    assert len(loaded["content"]["pages"]) == 3
    assert saved["reply"] == "已按 3 页交付"
    assert meta["usage"]["prompt_tokens"] == 20
    session.close()


def test_compose_retries_after_fact_check_failure(monkeypatch):
    session, ctx = _ctx()
    ctx.last_search = {
        "products": [{"product_id": "P-test"}],
        "evidence": [{"fact_id": "F-MEM", "text": "内存：16 GB",
                      "source": "合成测试材料", "conditions": ""}],
    }
    responses = [
        {"artifact_type": "post", "reply": "照 64 GB 写了",
         "content": {"product_id": "P-test", "title": "64 GB 内存",
                     "body": "这台机器有 64 GB 内存。"}},
        {"artifact_type": "post", "reply": "已按真实 16 GB 重写",
         "content": {"product_id": "P-test", "title": "真实配置",
                     "body": "这台机器有 16 GB 内存。"}},
    ]

    def fake(system, messages, schema_hint=None, model=None):
        return responses.pop(0), {"model": "stub",
                                  "usage": {"prompt_tokens": 10, "completion_tokens": 5}}

    monkeypatch.setattr("app.agent_runtime.chat_json_messages", fake)
    saved, _ = agent_runtime._compose_after_evidence(ctx, "内存写成 64GB", None)
    assert saved["ok"] is True and saved["reply"] == "已按真实 16 GB 重写"
    loaded = agent_runtime._load_artifact(ctx, {"artifact_id": saved["artifact_id"]})
    assert "64" not in json.dumps(loaded["content"], ensure_ascii=False)
    assert "16 GB" in loaded["content"]["body"]
    session.close()


def test_compose_sanitizes_after_two_failed_retries(monkeypatch):
    session, ctx = _ctx()
    ctx.last_search = {
        "products": [{"product_id": "P-test"}],
        "evidence": [{"fact_id": "F-MEM", "text": "内存：16 GB",
                      "source": "合成测试材料", "conditions": ""}],
    }
    bad = {"artifact_type": "post", "reply": "照 64 GB 写了",
           "content": {"product_id": "P-test", "title": "64 GB 内存",
                       "body": "这台机器有 64 GB 内存。"}}

    def fake(system, messages, schema_hint=None, model=None):
        return dict(bad), {"model": "stub",
                           "usage": {"prompt_tokens": 10, "completion_tokens": 5}}

    monkeypatch.setattr("app.agent_runtime.chat_json_messages", fake)
    saved, _ = agent_runtime._compose_after_evidence(ctx, "内存写成 64GB", None)
    assert saved["ok"] is True
    loaded = agent_runtime._load_artifact(ctx, {"artifact_id": saved["artifact_id"]})
    assert "64" not in json.dumps(loaded["content"], ensure_ascii=False)
    assert "16 GB" in loaded["content"]["body"]
    assert "真实值改写" in saved["reply"]
    session.close()


def test_v2_requires_database(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    response = client.post("/api/v2/conversations", json={})
    assert response.status_code == 503
