"""LangGraph runtime (``langgraph_autonomous_v1``) mechanism tests.

These are *integration* tests through the real v2 HTTP surface with a scripted model,
so they exercise the graph, the ledgers, the checkpointer and the existing business
services together. They are deliberately separate from the 180 legacy tests, which
stay untouched as the legacy contract.

No real model request is made and no credential is required.
"""
import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app import db as db_layer, models
from app.contracts import Fact, Source
from app.main import app
from app.runtime import RUNNER_VERSION, runner as lg_runner

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
    monkeypatch.setenv("LG_CHECKPOINT_PATH", str(tmp_path / "checkpoints.sqlite"))
    monkeypatch.setenv("LG_MAX_MODEL_CALLS", "6")
    # Each test gets a fresh saver bound to its own checkpoint file.
    monkeypatch.setattr(lg_runner, "_savers", {})
    db_layer.reset_for_tests(url)
    # A stale init flag from another test would leave this engine without tables.
    monkeypatch.setattr("app.main._workspace_db_ready", False)

    async def fake_catalog(*args, **kwargs):
        return FakeCatalog()

    monkeypatch.setattr("app.agent_runtime.load_catalog", fake_catalog)
    yield


def _script(monkeypatch, results):
    """Queue scripted native-protocol model replies."""
    calls = []

    def fake(system, messages, tools, model=None):
        calls.append({"messages": messages, "tools": tools})
        item = results.pop(0)
        if isinstance(item, Exception):
            raise item
        return item, {"provider": "stub", "model": model or "stub",
                      "usage": item.pop("_usage", {}) if isinstance(item, dict) else {}}

    monkeypatch.setattr("app.runtime.model_gateway.chat_tools", fake)
    return calls


def _call(name, args, call_id="c1"):
    return {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}


def _new_conversation(mode=None):
    payload = {} if mode is None else {"mode": mode}
    return client.post("/api/v2/conversations", json=payload).json()


def _send(cid, message, key=None):
    body = {"message": message}
    if key:
        body["idempotency_key"] = key
    return client.post(f"/api/v2/conversations/{cid}/messages", json=body)


# ---------------------------------------------------------------- runner selection

def test_new_conversation_defaults_to_langgraph_runner():
    data = _new_conversation()
    assert data["mode"] == RUNNER_VERSION
    assert data["runner_version"] == RUNNER_VERSION


def test_legacy_modes_remain_selectable():
    assert _new_conversation("guided")["mode"] == "guided"
    assert _new_conversation("autonomous")["mode"] == "autonomous"


def test_unknown_mode_falls_back_to_langgraph_runner():
    assert _new_conversation("something-else")["mode"] == RUNNER_VERSION


def test_langgraph_path_does_not_call_the_legacy_loop(monkeypatch):
    """The graph must not be a wrapper around agent_runtime.run_turn."""
    def boom(*args, **kwargs):
        raise AssertionError("legacy run_turn must not be used by the LG runner")

    monkeypatch.setattr("app.agent_runtime.run_turn", boom)
    _script(monkeypatch, [{"content": "好的，直接给你一版。", "tool_calls": []}])
    cid = _new_conversation()["conversation_id"]
    response = _send(cid, "随便给个标题")
    assert response.status_code == 200
    assert response.json()["status"] == "completed"


# ---------------------------------------------------------------- request idempotency

def test_same_key_same_payload_replays_the_same_run(monkeypatch):
    _script(monkeypatch, [{"content": "第一版。", "tool_calls": []}])
    cid = _new_conversation()["conversation_id"]
    first = _send(cid, "写个标题", key="K1").json()
    second = _send(cid, "写个标题", key="K1").json()
    assert first["run_id"] == second["run_id"]
    conversation = client.get(f"/api/v2/conversations/{cid}").json()
    assert [m["role"] for m in conversation["messages"]].count("user") == 1


def test_same_key_different_payload_conflicts(monkeypatch):
    _script(monkeypatch, [{"content": "第一版。", "tool_calls": []}])
    cid = _new_conversation()["conversation_id"]
    _send(cid, "写个标题", key="K1")
    conflict = _send(cid, "完全不同的一句", key="K1")
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "REQUEST_CONFLICT"


def test_same_key_is_independent_across_conversations(monkeypatch):
    _script(monkeypatch, [{"content": "A 版。", "tool_calls": []},
                          {"content": "B 版。", "tool_calls": []}])
    first = _new_conversation()["conversation_id"]
    second = _new_conversation()["conversation_id"]
    a = _send(first, "同一句话", key="SAME").json()
    b = _send(second, "同一句话", key="SAME").json()
    assert a["run_id"] != b["run_id"]
    assert a["reply"] == "A 版。" and b["reply"] == "B 版。"


# ---------------------------------------------------------------- metering

def test_missing_usage_is_unknown_not_zero(monkeypatch):
    _script(monkeypatch, [{"content": "完成。", "tool_calls": []}])
    cid = _new_conversation()["conversation_id"]
    data = _send(cid, "写个标题").json()
    assert data["usage"]["usage_certainty"] == "unknown"
    assert data["usage"]["unknown_attempts"] == 1
    assert data["usage"]["input_tokens"] == 0          # sum over *measured* attempts
    assert data["usage"]["measured_attempts"] == 0     # nothing was measured

    session = db_layer.session_factory()()
    rows = session.query(models.AgentModelAttempt).filter(
        models.AgentModelAttempt.conversation_id == cid).all()
    assert len(rows) == 1
    assert rows[0].prompt_tokens is None and rows[0].completion_tokens is None
    assert rows[0].usage_certainty == "unknown"
    session.close()


def test_reported_usage_is_metered(monkeypatch):
    _script(monkeypatch, [{"content": "完成。", "tool_calls": [],
                           "_usage": {"prompt_tokens": 120, "completion_tokens": 30}}])
    cid = _new_conversation()["conversation_id"]
    data = _send(cid, "写个标题").json()
    assert data["usage"]["usage_certainty"] == "reported"
    assert data["usage"]["input_tokens"] == 120 and data["usage"]["output_tokens"] == 30


def test_every_decision_is_one_attempt(monkeypatch):
    """A tool round-trip costs two model calls; both must be recorded."""
    _script(monkeypatch, [
        {"content": "", "tool_calls": [_call("read_product_evidence", {"product_id": "P-test"})]},
        {"content": "读取完成。", "tool_calls": []},
    ])
    cid = _new_conversation()["conversation_id"]
    data = _send(cid, "看看这台机器").json()
    assert data["usage"]["attempts"] == 2


# ---------------------------------------------------------------- pause / resume

def test_ask_user_pauses_then_resumes_on_the_same_thread(monkeypatch):
    _script(monkeypatch, [{"content": "", "tool_calls": [
        _call("ask_user", {"question": "这篇内容给谁看？", "options": ["学生", "办公"]})]}])
    cid = _new_conversation()["conversation_id"]
    first = _send(cid, "帮我写点内容").json()
    assert first["status"] == "waiting_user"
    assert first["reply"] == "这篇内容给谁看？"

    # A new user message becomes a new run that resumes the paused thread.
    _script(monkeypatch, [{"content": "好，面向刚毕业的设计师。", "tool_calls": []}])
    second = _send(cid, "刚毕业的设计师，不在选项里").json()
    assert second["status"] == "completed"
    assert second["run_id"] != first["run_id"]
    assert "设计师" in second["reply"]

    session = db_layer.session_factory()()
    messages = session.query(models.AgentMessage).filter(
        models.AgentMessage.conversation_id == cid).order_by(
        models.AgentMessage.seq).all()
    texts = [m.content for m in messages if m.role == "user"]
    assert "刚毕业的设计师，不在选项里" in texts
    session.close()


def test_resume_accepts_free_text_outside_the_options(monkeypatch):
    _script(monkeypatch, [{"content": "", "tool_calls": [
        _call("ask_user", {"question": "面向谁？", "options": ["学生"]})]}])
    cid = _new_conversation()["conversation_id"]
    assert _send(cid, "写点东西").json()["status"] == "waiting_user"
    _script(monkeypatch, [{"content": "收到。", "tool_calls": []}])
    data = _send(cid, "退休教师，选项里没有").json()
    assert data["status"] == "completed"


# ---------------------------------------------------------------- artifacts & effects

def _artifact_script(title="校园选本", body="内存 16 GB"):
    return {"artifact_type": "post",
            "content": {"product_id": "P-test", "title": title, "body": body}}


def test_artifact_is_saved_and_checked(monkeypatch):
    _script(monkeypatch, [
        {"content": "", "tool_calls": [_call("read_product_evidence", {"product_id": "P-test"})]},
        {"content": "", "tool_calls": [_call("save_artifact",
                                             {"artifact_type": "post", "product_id": "P-test",
                                              "content": {"product_id": "P-test",
                                                          "title": "校园选本",
                                                          "body": "内存 16 GB"}}, "c2")]},
        {"content": "已经保存并核对过了。", "tool_calls": []},
    ])
    cid = _new_conversation()["conversation_id"]
    data = _send(cid, "给这台机器写一篇").json()
    assert data["status"] == "completed"
    conversation = client.get(f"/api/v2/conversations/{cid}").json()
    assert len(conversation["artifacts"]) == 1
    assert conversation["artifacts"][0]["check"]["passed"] is True


def test_unsupported_parameter_is_rejected_by_grounding(monkeypatch):
    _script(monkeypatch, [
        {"content": "", "tool_calls": [_call("read_product_evidence", {"product_id": "P-test"})]},
        {"content": "", "tool_calls": [_call("save_artifact",
                                             {"artifact_type": "post", "product_id": "P-test",
                                              "content": {"product_id": "P-test",
                                                          "title": "64 GB 内存",
                                                          "body": "这台机器有 64 GB 内存。"}}, "c2")]},
        {"content": "没有 64 GB 这个配置。", "tool_calls": []},
    ])
    cid = _new_conversation()["conversation_id"]
    data = _send(cid, "内存写成 64GB").json()
    conversation = client.get(f"/api/v2/conversations/{cid}").json()
    assert conversation["artifacts"] == []
    assert any(event.get("tool") == "save_artifact" and event.get("ok") is False
               for event in data["events"])


def test_effect_ledger_prevents_a_duplicate_revision_on_replay(monkeypatch):
    """A replayed save_artifact call id must reuse the recorded outcome."""
    _script(monkeypatch, [
        {"content": "", "tool_calls": [_call("read_product_evidence", {"product_id": "P-test"})]},
        {"content": "", "tool_calls": [_call("save_artifact",
                                             {"artifact_type": "post", "product_id": "P-test",
                                              "content": {"product_id": "P-test",
                                                          "title": "校园选本",
                                                          "body": "内存 16 GB"}}, "same-call")]},
        {"content": "完成。", "tool_calls": []},
    ])
    cid = _new_conversation()["conversation_id"]
    _send(cid, "给我一版")
    session = db_layer.session_factory()()
    rows = session.query(models.AgentEffect).filter(
        models.AgentEffect.conversation_id == cid).all()
    assert len(rows) == 1 and rows[0].state == "completed"
    session.close()


# ---------------------------------------------------------------- cancel & budget

def test_cancel_stops_before_the_next_tool(monkeypatch):
    """A cancel that lands while a model call is in flight stops the next step."""
    holder = {"cid": ""}
    state = {"calls": 0}

    def fake(system, messages, tools, model=None):
        state["calls"] += 1
        if state["calls"] > 1:
            raise AssertionError("no further model call may be spent after a cancel")
        session = db_layer.session_factory()()
        run = session.query(models.AgentRun).filter(
            models.AgentRun.conversation_id == holder["cid"]).order_by(
            models.AgentRun.created_at.desc()).first()
        run.status = "cancelled"
        session.commit()
        session.close()
        return ({"content": "", "tool_calls": [
            _call("read_product_evidence", {"product_id": "P-test"})]},
            {"model": "stub", "usage": {}})

    monkeypatch.setattr("app.runtime.model_gateway.chat_tools", fake)
    cid = _new_conversation()["conversation_id"]
    holder["cid"] = cid
    data = _send(cid, "开始做").json()
    assert data["status"] == "cancelled"
    assert state["calls"] == 1
    assert client.get(f"/api/v2/conversations/{cid}").json()["artifacts"] == []


def test_model_call_budget_ends_as_partial_not_completed(monkeypatch, tmp_path):
    monkeypatch.setenv("LG_MAX_MODEL_CALLS", "1")
    # The model keeps asking for one more evidence read; the budget stops it.
    _script(monkeypatch, [
        {"content": "", "tool_calls": [_call("read_product_evidence", {"product_id": "P-test"})]},
        {"content": "", "tool_calls": [_call("read_product_evidence", {"product_id": "P-test"})]},
    ])
    cid = _new_conversation()["conversation_id"]
    data = _send(cid, "反复查资料").json()
    assert data["status"] == "partial"
    assert data["stop_reason"] in {"model_call_budget", "time_budget"}


# ---------------------------------------------------------------- output fence

def test_output_fence_sanitizes_unsupported_numbers_in_the_reply(monkeypatch):
    _script(monkeypatch, [
        {"content": "", "tool_calls": [_call("read_product_evidence", {"product_id": "P-test"})]},
        {"content": "这台机器有 64 GB 内存，值得买。", "tool_calls": []},
    ])
    cid = _new_conversation()["conversation_id"]
    data = _send(cid, "说说内存").json()
    assert "64" not in data["reply"]
    assert "16 GB" in data["reply"]
    assert data["status"] == "partial"


def test_output_fence_leaves_clean_creative_text_alone(monkeypatch):
    _script(monkeypatch, [{"content": "给你 3 个标题方向，7 天后再发。", "tool_calls": []}])
    cid = _new_conversation()["conversation_id"]
    data = _send(cid, "给标题").json()
    assert data["status"] == "completed"
    assert "3 个标题" in data["reply"]


# ---------------------------------------------------------------- messages & deletion

def test_message_sequence_has_no_collisions(monkeypatch):
    """The legacy batch formula collided; the new runner uses a monotonic counter."""
    _script(monkeypatch, [
        {"content": "", "tool_calls": [_call("read_product_evidence", {"product_id": "P-test"}, "a1")]},
        {"content": "", "tool_calls": [_call("find_product_images", {"product_id": "P-test"}, "a2")]},
        {"content": "完成。", "tool_calls": []},
    ])
    cid = _new_conversation()["conversation_id"]
    _send(cid, "查资料再交付")
    session = db_layer.session_factory()()
    seqs = [row.seq for row in session.query(models.AgentMessage).filter(
        models.AgentMessage.conversation_id == cid).all()]
    assert len(seqs) == len(set(seqs))
    assert seqs == sorted(seqs)
    session.close()


def test_delete_purges_ledgers_and_checkpoint(monkeypatch):
    _script(monkeypatch, [{"content": "完成。", "tool_calls": []}])
    cid = _new_conversation()["conversation_id"]
    _send(cid, "写个标题", key="K1")
    session = db_layer.session_factory()()
    assert session.query(models.AgentModelAttempt).filter(
        models.AgentModelAttempt.conversation_id == cid).count() == 1
    session.close()

    response = client.post(f"/api/v2/conversations/{cid}/delete")
    assert response.status_code == 200
    assert response.json()["checkpoint_purged"] is True
    assert client.get(f"/api/v2/conversations/{cid}").status_code == 404

    session = db_layer.session_factory()()
    for table in (models.AgentModelAttempt, models.AgentEffect, models.AgentRequestRecord):
        assert session.query(table).filter(table.conversation_id == cid).count() == 0
    session.close()


def test_rename_and_pin_survive_the_new_runner(monkeypatch):
    """0.16 task history must keep working for LangGraph conversations."""
    _script(monkeypatch, [{"content": "完成。", "tool_calls": []}])
    cid = _new_conversation()["conversation_id"]
    _send(cid, "写个校园选题")
    updated = client.post(f"/api/v2/conversations/{cid}/update",
                          json={"title": "校园选题 LG", "pinned": True}).json()
    assert updated["title"] == "校园选题 LG" and updated["pinned"] is True
    listing = client.get("/api/v2/conversations?limit=50").json()["conversations"]
    assert listing[0]["conversation_id"] == cid


def test_runtime_view_is_redacted(monkeypatch):
    _script(monkeypatch, [{"content": "完成。", "tool_calls": []}])
    cid = _new_conversation()["conversation_id"]
    _send(cid, "写个标题")
    view = client.get(f"/api/v2/conversations/{cid}/runtime").json()
    assert view["runner_version"] == RUNNER_VERSION
    text = json.dumps(view, ensure_ascii=False)
    assert "api_key" not in text.lower() and "Session" not in text


# ---------------------------------------------------------------- protocol fallback

def test_json_compat_fallback_when_native_tools_are_unsupported(monkeypatch):
    from app.model import ModelError

    _script(monkeypatch, [ModelError("模型服务 HTTP 400")])

    def fake_json(system, messages, schema_hint="", model=None):
        return {"action": "reply", "reply": "用兼容协议回答。"}, {"model": "stub", "usage": {}}

    monkeypatch.setattr("app.runtime.model_gateway.chat_json_messages", fake_json)
    cid = _new_conversation()["conversation_id"]
    data = _send(cid, "你好").json()
    assert data["status"] == "completed"
    assert data["reply"] == "用兼容协议回答。"

    session = db_layer.session_factory()()
    row = session.query(models.AgentModelAttempt).filter(
        models.AgentModelAttempt.conversation_id == cid).one()
    assert row.protocol_mode == "json_compat" and row.ok is True
    session.close()


def test_model_failure_is_reported_not_faked_as_success(monkeypatch):
    from app.model import ModelError

    _script(monkeypatch, [ModelError("模型连接或读取超时，请检查服务地址和网络")])
    cid = _new_conversation()["conversation_id"]
    data = _send(cid, "写个标题").json()
    assert data["status"] == "failed"
    assert "不可用" in data["reply"]
    assert client.get(f"/api/v2/conversations/{cid}").json()["artifacts"] == []


# ---------------------------------------------------------------- error hygiene

def test_public_error_never_leaks_internal_configuration():
    from app.runtime.errors import KNOWLEDGE_UNAVAILABLE, error_marker, public_error, \
        sanitize_observation

    raw = "未配置IMA凭证，请在服务端环境变量或本机 ~/.config/ima 中配置"
    safe = public_error(raw)
    assert safe == KNOWLEDGE_UNAVAILABLE
    for token in ["IMA", ".config", "环境变量", "~/", "LLM_", "DATABASE_URL", "/Users"]:
        assert token not in safe

    assert error_marker(raw) == "knowledge_unavailable"
    assert error_marker("模型连接或读取超时，请检查服务地址和网络") == "model_unavailable"
    assert error_marker("ImaError") == "ImaError"

    cleaned = sanitize_observation({"ok": False, "error": "TOOL_ERROR", "message": raw})
    assert cleaned["message"] == safe and cleaned["internal_detail_redacted"] is True


def test_knowledge_base_failure_keeps_the_turn_alive(monkeypatch):
    """A KB outage must degrade to one tool error the model can react to.

    It must not abort the whole turn before the first sentence is written, and the
    user-facing text must stay free of configuration instructions.
    """
    from app.ima import ImaError

    async def broken_catalog(*args, **kwargs):
        raise ImaError("未配置IMA凭证，请在服务端环境变量或本机 ~/.config/ima 中配置")

    monkeypatch.setattr("app.agent_runtime.load_catalog", broken_catalog)

    seen = {"messages": []}

    def fake(system, messages, tools, model=None):
        seen["messages"] = messages
        if not any(item.get("role") == "tool" for item in messages):
            return ({"content": "", "tool_calls": [
                _call("search_products", {"query": "MateBook"})]},
                {"model": "stub", "usage": {}})
        return ({"content": "知识库连不上，我先给你一个不依赖参数的标题方向。",
                 "tool_calls": []}, {"model": "stub", "usage": {}})

    monkeypatch.setattr("app.runtime.model_gateway.chat_tools", fake)
    cid = _new_conversation()["conversation_id"]
    data = _send(cid, "给大学生写一篇").json()

    assert data["status"] == "completed"
    assert "不依赖参数" in data["reply"]
    transcript = json.dumps(seen["messages"], ensure_ascii=False)
    assert "~/.config" not in transcript and "环境变量" not in transcript
    assert "知识库当前不可用" in transcript


def test_model_failure_message_to_the_user_is_sanitized(monkeypatch):
    from app.model import ModelError

    _script(monkeypatch, [ModelError("请先配置模型端点与密钥，LLM_API_KEY 为空")])
    cid = _new_conversation()["conversation_id"]
    data = _send(cid, "写个标题").json()
    assert data["status"] == "failed"
    assert "LLM_API_KEY" not in data["reply"]
    assert "密钥" not in data["reply"]


# ---------------------------------------------------------------- work panel wiring

def test_candidates_are_surfaced_for_the_work_panel(monkeypatch):
    _script(monkeypatch, [
        {"content": "", "tool_calls": [_call("search_products", {"query": "MateBook"})]},
        {"content": "知识库里有这款。", "tool_calls": []},
    ])
    cid = _new_conversation()["conversation_id"]
    data = _send(cid, "有哪些型号").json()
    assert data["candidates"] and data["candidates"][0]["model"] == "HUAWEI MateBook Test"
    conversation = client.get(f"/api/v2/conversations/{cid}").json()
    assert conversation["task"]["candidates"][0]["product_id"] == "P-test"


def test_original_images_are_surfaced_for_the_work_panel(monkeypatch):
    class FakeImage:
        image_id = "IMG-1"
        caption = "HUAWEI MateBook Test 官方产品图"
        access_url = "/api/v1/assets/IMG-1"

    async def fake_images(*args, **kwargs):
        return [FakeImage()], []

    monkeypatch.setattr("app.agent_runtime.verified_images", fake_images)
    _script(monkeypatch, [
        {"content": "", "tool_calls": [_call("find_product_images", {"product_id": "P-test"})]},
        {"content": "配图用的是知识库原图，没有重画产品外观。", "tool_calls": []},
    ])
    cid = _new_conversation()["conversation_id"]
    data = _send(cid, "找几张原图").json()
    assert data["images"] and data["images"][0]["access_url"] == "/api/v1/assets/IMG-1"
    conversation = client.get(f"/api/v2/conversations/{cid}").json()
    assert conversation["task"]["images"][0]["image_id"] == "IMG-1"


def test_question_reference_options_surface_as_actions(monkeypatch):
    _script(monkeypatch, [{"content": "", "tool_calls": [
        _call("ask_user", {"question": "这篇内容给谁看？",
                           "options": ["大学生", "职场办公"]})]}])
    cid = _new_conversation()["conversation_id"]
    data = _send(cid, "写点东西").json()
    assert data["status"] == "waiting_user"
    # Reference options are offered but never mandatory.
    assert data["actions"] == ["大学生", "职场办公"]


def test_frontend_does_not_pin_the_legacy_runner():
    """F01 regression guard: changing only the backend default changes nothing.

    The entry point must let the server own the default runner version, otherwise the
    UI silently keeps creating guided conversations.
    """
    from pathlib import Path

    from app import main as main_module

    source = (Path(main_module.__file__).parent / "static" / "app.js").read_text()
    assert "mode: 'guided'" not in source
    assert 'mode: "guided"' not in source
    assert "mode: 'autonomous'" not in source


def test_task_restore_renders_candidates_without_a_guided_stage():
    """The autonomous runner has no select_product stage; the UI must not require one."""
    from pathlib import Path

    from app import main as main_module

    source = (Path(main_module.__file__).parent / "static" / "app.js").read_text()
    assert "conversation.stage === 'select_product'" not in source


# ---------------------------------------------------------------- async mode / recovery

def test_async_mode_returns_202_and_the_worker_finishes_the_run(monkeypatch):
    """response_mode=async must submit, return 202, and let the worker complete it.

    A persistent client loop is required: background tasks do not survive a per-request
    event loop, which is exactly why the app opens its saver in the lifespan.
    """
    import time

    _script(monkeypatch, [{"content": "后台完成了。", "tool_calls": []}])
    with TestClient(app) as async_client:
        cid = async_client.post("/api/v2/conversations", json={}).json()["conversation_id"]
        response = async_client.post(f"/api/v2/conversations/{cid}/messages",
                                     json={"message": "随便来点", "response_mode": "async"})
        assert response.status_code == 202
        body = response.json()
        assert body["status"] == "running" and body["runner_version"] == RUNNER_VERSION
        assert body["events_url"].endswith("/events")
        run_id = body["run_id"]

        # An independent connection can already see the run while it executes.
        running = async_client.get(f"/api/v2/runs/{run_id}/events").json()
        assert running["status"] in {"running", "completed"}

        final = running
        for _ in range(60):
            if final["status"] != "running":
                break
            time.sleep(0.05)
            final = async_client.get(f"/api/v2/runs/{run_id}/events").json()
        assert final["status"] == "completed"
        conversation = async_client.get(f"/api/v2/conversations/{cid}").json()
        assert any(message["content"] == "后台完成了。"
                   for message in conversation["messages"])


def test_unknown_response_mode_falls_back_to_blocking(monkeypatch):
    _script(monkeypatch, [{"content": "阻塞模式完成。", "tool_calls": []}])
    cid = _new_conversation()["conversation_id"]
    response = _send(cid, "写个标题")
    assert response.status_code == 200
    assert response.json()["reply"] == "阻塞模式完成。"


def test_orphaned_runs_are_not_reported_as_success():
    """A run left 'running' by a restart has an unknown outcome, not a successful one."""
    conversation = _new_conversation()
    cid = conversation["conversation_id"]
    session = db_layer.session_factory()()
    session.add(models.AgentRun(conversation_id=cid, status="running"))
    session.commit()
    recovered = lg_runner.recover_orphaned_runs(session)
    session.commit()
    assert recovered == 1
    run = session.query(models.AgentRun).filter(
        models.AgentRun.conversation_id == cid).one()
    assert run.status == "needs_reconciliation"
    assert run.stop_reason == "orphaned_by_restart"
    session.close()


def test_concurrent_turns_on_one_conversation_serialize(monkeypatch):
    """The conversation lease keeps two in-flight turns from interleaving state."""
    import threading

    seen = []

    def fake(system, messages, tools, model=None):
        seen.append(1)
        return {"content": f"第 {len(seen)} 次回答。", "tool_calls": []}, {"model": "stub",
                                                                        "usage": {}}

    monkeypatch.setattr("app.runtime.model_gateway.chat_tools", fake)
    cid = _new_conversation()["conversation_id"]
    results = []

    def send(text):
        results.append(_send(cid, text).json())

    threads = [threading.Thread(target=send, args=(f"消息{i}",)) for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(results) == 2
    seqs = [row[0] for row in db_layer.session_factory()().query(
        models.AgentMessage.seq).filter(models.AgentMessage.conversation_id == cid).all()]
    assert len(seqs) == len(set(seqs))


def test_event_stream_replays_persisted_events_with_a_cursor(monkeypatch):
    """SSE replay is idempotent: after_seq resumes without duplicating events."""
    _script(monkeypatch, [{"content": "完成。", "tool_calls": []}])
    with TestClient(app) as stream_client:
        cid = stream_client.post("/api/v2/conversations", json={}).json()["conversation_id"]
        run_id = stream_client.post(f"/api/v2/conversations/{cid}/messages",
                                    json={"message": "写个标题"}).json()["run_id"]

        with stream_client.stream(
                "GET", f"/api/v2/runs/{run_id}/events/stream?after_seq=0") as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            full = "".join(response.iter_text())
        assert "event: end" in full
        assert "run_finished" in full
        assert "id: 0" in full

        # Reconnecting past the end yields no replayed payload events.
        with stream_client.stream(
                "GET", f"/api/v2/runs/{run_id}/events/stream?after_seq=99") as response:
            resumed = "".join(response.iter_text())
        assert "run_finished" not in resumed
        assert "event: end" in resumed


def test_event_stream_rejects_unknown_run():
    response = client.get("/api/v2/runs/does-not-exist/events/stream")
    assert response.status_code == 404


# ---------------------------------------------------------------- checkpoint privacy

def test_checkpoint_holds_references_not_secrets(monkeypatch):
    """The checkpoint is read back out-of-process, so it cannot depend on the app loop."""
    import sqlite3

    _script(monkeypatch, [{"content": "完成。", "tool_calls": []}])
    cid = _new_conversation()["conversation_id"]
    _send(cid, "写个标题")

    path = lg_runner.checkpoint_path()
    connection = sqlite3.connect(path)
    blobs = []
    for (name,) in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"):
        for row in connection.execute(f"SELECT * FROM {name}"):
            blobs.append(str(row))
    connection.close()
    text = "\n".join(blobs)
    assert cid in text, "the conversation should be traceable in its own thread"
    assert "LLM_API_KEY" not in text
    assert "api_key" not in text.lower()
    assert "sqlalchemy" not in text.lower()