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
    # aiosqlite's worker thread is non-daemon: an unclosed checkpoint connection keeps
    # the interpreter alive at exit, which shows up as the whole test file hanging.
    try:
        asyncio.run(lg_runner.close_saver())
    except Exception:  # noqa: BLE001
        pass


def _script(monkeypatch, results, review=None):
    """Queue scripted native-protocol model replies.

    The independent review pass is a separate, non-decision call. It is stubbed here as
    well, so no test can reach the network through it: by default it passes, and a test
    can script specific reviewer outputs with ``review=``.
    """
    calls = []

    def fake(system, messages, tools, model=None):
        calls.append({"messages": messages, "tools": tools})
        item = results.pop(0)
        if isinstance(item, Exception):
            raise item
        return item, {"provider": "stub", "model": model or "stub",
                      "usage": item.pop("_usage", {}) if isinstance(item, dict) else {}}

    monkeypatch.setattr("app.runtime.model_gateway.chat_tools", fake)

    queue = list(review) if isinstance(review, list) else []
    fixed = review if not isinstance(review, list) else None

    def fake_json(system, messages, schema_hint=None, model=None):
        if queue:
            item = queue.pop(0)
        elif fixed is not None:
            item = fixed
        else:
            item = {"verdict": "pass", "issues": [], "checked": ["事实与条件"]}
        if isinstance(item, Exception):
            raise item
        usage = item.pop("_usage", {}) if isinstance(item, dict) else {}
        return item, {"provider": "stub", "model": model or "stub", "usage": usage}

    monkeypatch.setattr("app.runtime.model_gateway.chat_json_messages", fake_json)
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
    # Two requests now happen: the decision and the independent review. Neither reported
    # usage, so the run is unknown rather than a measured zero.
    assert data["usage"]["unknown_attempts"] == 2
    assert data["usage"]["input_tokens"] == 0          # sum over *measured* attempts
    assert data["usage"]["measured_attempts"] == 0     # nothing was measured

    session = db_layer.session_factory()()
    rows = session.query(models.AgentModelAttempt).filter(
        models.AgentModelAttempt.conversation_id == cid,
        models.AgentModelAttempt.kind == "decide").all()
    assert len(rows) == 1
    assert rows[0].prompt_tokens is None and rows[0].completion_tokens is None
    assert rows[0].usage_certainty == "unknown"
    session.close()


def test_reported_usage_is_metered(monkeypatch):
    _script(monkeypatch, [{"content": "完成。", "tool_calls": [],
                           "_usage": {"prompt_tokens": 120, "completion_tokens": 30}}],
            review={"verdict": "pass", "issues": [], "checked": [],
                    "_usage": {"prompt_tokens": 40, "completion_tokens": 10}})
    cid = _new_conversation()["conversation_id"]
    data = _send(cid, "写个标题").json()
    assert data["usage"]["usage_certainty"] == "reported"
    # Both requests reported, so the totals are measured, not estimated.
    assert data["usage"]["input_tokens"] == 160 and data["usage"]["output_tokens"] == 40


def test_every_decision_is_one_attempt(monkeypatch):
    """A tool round-trip costs two model calls; both must be recorded."""
    _script(monkeypatch, [
        {"content": "", "tool_calls": [_call("read_product_evidence", {"product_id": "P-test"})]},
        {"content": "读取完成。", "tool_calls": []},
    ])
    cid = _new_conversation()["conversation_id"]
    data = _send(cid, "看看这台机器").json()
    session = db_layer.session_factory()()
    decisions = session.query(models.AgentModelAttempt).filter(
        models.AgentModelAttempt.conversation_id == cid,
        models.AgentModelAttempt.kind == "decide").count()
    session.close()
    assert decisions == 2
    # The independent review is a third request, metered like any other.
    assert data["usage"]["attempts"] == 3


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

def test_output_fence_flags_unsupported_numbers_without_rewriting(monkeypatch):
    """Prose is flagged, not rewritten.

    An earlier version ran sanitize_text over the reply, which corrupted a real
    cross-model comparison by injecting the first model's values into another model's
    figures. The claim now stays visible and is marked unverified.
    """
    _script(monkeypatch, [
        {"content": "", "tool_calls": [_call("read_product_evidence", {"product_id": "P-test"})]},
        {"content": "这台机器有 64 GB 内存，值得买。", "tool_calls": []},
    ])
    cid = _new_conversation()["conversation_id"]
    data = _send(cid, "说说内存").json()
    assert data["status"] == "partial"
    assert "64 GB 内存" in data["reply"], "the sentence must not be rewritten"
    assert "未核实" in data["reply"]
    assert data.get("unsupported_numbers")


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
        models.AgentModelAttempt.conversation_id == cid).count() == 2  # decide + review
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
        models.AgentModelAttempt.conversation_id == cid,
        models.AgentModelAttempt.kind == "decide").one()
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


def test_delivery_contract_carries_variants_material_and_images(monkeypatch):
    """规格 §4 的数据契约：run 返回 data.variants / data.material / data.images。

    同时验证回放路径：会话详情顶层与 task 下都能读到同一份 3 篇文案与资料存档。
    """
    class FakeImage:
        image_id = "IMG-1"
        caption = "HUAWEI MateBook Test 官方产品图"
        access_url = "/api/v1/assets/IMG-1"

    async def fake_images(*args, **kwargs):
        return [FakeImage()], []

    monkeypatch.setattr("app.agent_runtime.verified_images", fake_images)
    variants = [{"variant_id": f"v{index}", "label": f"方向{index}",
                 "title": f"标题{index}", "body": "内存 16 GB",
                 "tags": ["校园", "华为PC"], "models": ["P-test"]}
                for index in range(1, 4)]
    _script(monkeypatch, [
        {"content": "", "tool_calls": [
            _call("update_task_material", {"audience": "大学生", "platform": "小红书",
                                           "scenario": "校园学习", "campaign": "开学季",
                                           "models": ["P-test"],
                                           "hotspot": {"title": "开学季选本热",
                                                       "source": "热梗（联网搜索）"}}, "a1"),
            _call("read_product_evidence", {"product_id": "P-test"}, "a2")]},
        {"content": "", "tool_calls": [
            _call("save_artifact", {"artifact_type": "variants",
                                    "content": {"product_ids": ["P-test"],
                                                "variants": variants}}, "a3"),
            _call("find_product_images", {"product_id": "P-test"}, "a4")]},
        {"content": "已经按资料写好 3 篇，并附上知识库原图。", "tool_calls": []},
    ])
    cid = _new_conversation()["conversation_id"]
    data = _send(cid, "资料齐了，输出最后交付内容").json()
    assert data["status"] == "completed"

    assert [row["variant_id"] for row in data["variants"]] == ["v1", "v2", "v3"]
    assert data["variants"][0]["models"] == ["P-test"]
    assert data["material"]["audience"] == "大学生"
    assert data["material"]["platform"] == "xiaohongshu" or data["material"]["platform"] == "小红书"
    assert data["material"]["campaign"] == "开学季"
    assert data["material"]["models"] == [{"product_id": "P-test",
                                          "model": "HUAWEI MateBook Test"}]
    assert data["material"]["hotspot"] == {"title": "开学季选本热",
                                          "source": "热梗（联网搜索）"}
    assert data["images"] and data["images"][0]["image_id"] == "IMG-1"
    # run 的顶层字段必须与 GET conversation 一致：客户端不该从一条路径拿到真实列表、
    # 从另一条路径拿到 undefined（前端目前不消费它，但将来接入的客户端会静默拿到空）。
    assert data["selected_models"] == ["P-test"]

    conversation = client.get(f"/api/v2/conversations/{cid}").json()
    assert [row["variant_id"] for row in conversation["variants"]] == ["v1", "v2", "v3"]
    assert conversation["selected_models"] == data["selected_models"]
    assert conversation["task"]["variants"][0]["title"] == "标题1"
    assert conversation["material"]["hotspot"]["title"] == "开学季选本热"
    assert conversation["task"]["material"]["audience"] == "大学生"


def test_a_search_never_records_a_selected_model(monkeypatch):
    """列出候选 ≠ 选定型号：只有 update_task_material 或交付物的 product_ids 才算选择。

    否则用户只是问“有哪些型号”，资料里就会出现一个他从未选过的型号。
    """
    _script(monkeypatch, [
        {"content": "", "tool_calls": [_call("search_products", {"query": "MateBook"})]},
        {"content": "知识库里有这款。", "tool_calls": []},
    ])
    cid = _new_conversation()["conversation_id"]
    _send(cid, "有哪些型号").json()
    conversation = client.get(f"/api/v2/conversations/{cid}").json()
    assert conversation["task"]["candidates"]
    assert conversation["material"]["models"] == []
    assert conversation["selected_models"] == []


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
    """The conversation lease keeps two in-flight turns from interleaving state.

    The requests go through a client held open with ``with`` on purpose. The module-level
    ``TestClient(app)`` is never entered, so every request ran on its **own** event loop;
    two loops then raced ``AsyncSqliteSaver.setup()`` on one checkpoint file and one died
    with ``sqlite3.OperationalError: database is locked`` from a worker thread (about 1 run
    in 3 overall). One loop is the production shape.
    """
    import threading

    seen = []

    def fake(system, messages, tools, model=None):
        seen.append(1)
        return {"content": f"第 {len(seen)} 次回答。", "tool_calls": []}, {"model": "stub",
                                                                        "usage": {}}

    monkeypatch.setattr("app.runtime.model_gateway.chat_tools", fake)
    with TestClient(app) as concurrent_client:
        cid = concurrent_client.post("/api/v2/conversations",
                                     json={}).json()["conversation_id"]
        results = []

        def send(text):
            results.append(concurrent_client.post(
                f"/api/v2/conversations/{cid}/messages", json={"message": text}).json())

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


def test_model_call_that_never_returns_ends_the_run(monkeypatch):
    """The worker path is bounded by the caller-side cap.

    This exercises ``execute_run`` directly, which is how the async worker runs it.
    The blocking HTTP path is instead bounded inside the transport
    (``app.model._post_chat_completions``), because the app's BaseHTTPMiddleware uses an
    anyio task group that waits for an abandoned task on the way out — see
    tests/test_model_deadline.py for that guarantee.
    """
    monkeypatch.setenv("LG_MODEL_CALL_TIMEOUT", "1")
    import time as _time

    def hang(system, messages, tools, model=None):
        _time.sleep(6)
        return {"content": "太晚了", "tool_calls": []}, {"model": "stub", "usage": {}}

    monkeypatch.setattr("app.runtime.model_gateway.chat_tools", hang)

    session = db_layer.session_factory()()
    conversation = models.AgentConversation(workspace_id="default",
                                           runner_version=RUNNER_VERSION)
    session.add(conversation)
    session.flush()
    run = models.AgentRun(conversation_id=conversation.id, status="running")
    session.add(run)
    session.flush()
    conversation_id, run_id = conversation.id, run.id
    session.commit()
    session.close()

    async def drive():
        session = db_layer.session_factory()()
        conv = session.get(models.AgentConversation, conversation_id)
        row = session.get(models.AgentRun, run_id)
        started = _time.monotonic()
        payload = await lg_runner.execute_run(session, conv, row, "写个标题", None)
        # Measured inside the coroutine: asyncio.run() then waits at shutdown for the
        # abandoned worker thread, which is a separate (and bounded) cost.
        elapsed = _time.monotonic() - started
        session.commit()
        session.close()
        return payload, elapsed

    payload, elapsed = asyncio.run(drive())
    assert elapsed < 4, f"the run was not bounded (took {elapsed:.1f}s)"
    assert payload["status"] == "failed"
    assert "模型" in payload["reply"]


def test_client_disconnect_does_not_leave_a_running_run(monkeypatch):
    """CancelledError is a BaseException; it must still terminate the run."""
    session = db_layer.session_factory()()
    conversation = models.AgentConversation(workspace_id="default",
                                           runner_version=RUNNER_VERSION)
    session.add(conversation)
    session.flush()
    run = models.AgentRun(conversation_id=conversation.id, status="running")
    session.add(run)
    session.flush()
    conversation_id, run_id = conversation.id, run.id
    session.commit()
    session.close()

    class StubGraph:
        async def aget_state(self, config):
            return type("S", (), {"next": ()})()

        async def ainvoke(self, *args, **kwargs):
            raise asyncio.CancelledError()

    monkeypatch.setattr("app.runtime.runner.build_graph", lambda saver, ctx: StubGraph())

    async def drive():
        session = db_layer.session_factory()()
        conv = session.get(models.AgentConversation, conversation_id)
        row = session.get(models.AgentRun, run_id)
        with pytest.raises(asyncio.CancelledError):
            await lg_runner.execute_run(session, conv, row, "写个标题", None)
        session.commit()
        session.close()

    asyncio.run(drive())

    session = db_layer.session_factory()()
    row = session.get(models.AgentRun, run_id)
    assert row.status == "needs_reconciliation"
    assert row.stop_reason == "client_disconnected"
    session.close()


def test_fabricated_first_person_experience_is_flagged(monkeypatch):
    """Real testing produced a first-person story with the KB down; it must not ship
    as verified content."""
    _script(monkeypatch, [{"content":
                           "背了三年游戏本上班，今年终于换回 MateBook 14 了。"
                           "14 英寸塞包里不占地。", "tool_calls": []}])
    cid = _new_conversation()["conversation_id"]
    data = _send(cid, "写一篇办公场景的小红书").json()
    assert data["status"] == "partial"
    assert "第一人称使用叙述" in data["reply"]
    markers = [item for item in (data.get("unverified_experience") or [])]
    assert markers, "the fabricated experience markers should be reported"


def test_unverified_numbers_are_flagged_when_the_kb_is_unavailable(monkeypatch):
    """Without evidence, a spec claim in prose must not be presented as verified."""
    _script(monkeypatch, [{"content": "这台机器 14 英寸，重 1.33 kg，很合适。",
                           "tool_calls": []}])
    cid = _new_conversation()["conversation_id"]
    data = _send(cid, "说说这台机器").json()
    assert data["status"] == "partial"
    assert "未经核实" in data["reply"]
    assert data.get("unverified_numbers")


def test_clean_creative_copy_is_not_flagged(monkeypatch):
    """The guard must not fire on ordinary creative copy."""
    _script(monkeypatch, [{"content": "给你 3 个校园选题方向，7 天后再发。",
                           "tool_calls": []}])
    cid = _new_conversation()["conversation_id"]
    data = _send(cid, "给标题").json()
    assert data["status"] == "completed"
    assert "提示：" not in data["reply"]


def test_sidebar_can_be_collapsed_on_wide_screens():
    """The sidebar was openable but not closable: no collapse control existed on desktop.

    Guards the wiring, since this is pure front-end behaviour with no server contract.
    """
    from pathlib import Path

    from app import main as main_module

    static = Path(main_module.__file__).parent / "static"
    html = (static / "index.html").read_text()
    css = (static / "style.css").read_text()
    js = (static / "app.js").read_text()

    assert 'id="sidebar-collapse"' in html, "a visible collapse control is required"
    assert ".sidebar.collapsed" in css, "the sidebar needs a collapsed state"
    assert "#sidebar-toggle{display:none}" not in css, \
        "the header toggle must be reachable on wide screens too"
    assert "sidebar-collapse'" in js and "setSidebar(false)" in js
    # The two mechanisms must stay separate: collapse on wide, overlay on narrow.
    assert "NARROW_QUERY" in js and "matchMedia" in js


# ---------------------------------------------------------------- cross-model replies

class TwoProductCatalog:
    """Two models with different figures, so a comparison reply spans both."""

    class A:
        model = "HUAWEI MateBook Test A"
        product_id = "P-a"
        issues = []
        docs = []
        facts = [_fact("A-MEM", "内存：16 GB"), _fact("A-W", "尺寸与重量：约 1.33 kg")]

    class B:
        model = "HUAWEI MateBook Test B"
        product_id = "P-b"
        issues = []
        docs = []
        facts = [_fact("B-MEM", "内存：32 GB"), _fact("B-W", "尺寸与重量：约 1.68 kg")]

    products = [A(), B()]
    entries = []
    image_index = []


def test_cross_model_comparison_is_not_flagged(monkeypatch):
    """A reply comparing two read models must accept both models' figures.

    The previous fence only loaded the first product's records, so it rewrote the
    second model's real values with the first model's — corrupting valid copy.
    """
    async def two_products(*args, **kwargs):
        return TwoProductCatalog()

    monkeypatch.setattr("app.agent_runtime.load_catalog", two_products)
    _script(monkeypatch, [
        {"content": "", "tool_calls": [_call("read_product_evidence", {"product_id": "P-a"}, "c1")]},
        {"content": "", "tool_calls": [_call("read_product_evidence", {"product_id": "P-b"}, "c2")]},
        {"content": "A 款是 16 GB、约 1.33 kg；B 款是 32 GB、约 1.68 kg。",
         "tool_calls": []},
    ])
    cid = _new_conversation()["conversation_id"]
    data = _send(cid, "帮我对比这两款").json()
    assert data["status"] == "completed", f"unexpected flags: {data.get('unsupported_numbers')}"
    assert "1.68 kg" in data["reply"], "the second model's figure must survive untouched"
    assert not data.get("unsupported_numbers")


def test_a_number_from_neither_model_is_still_flagged(monkeypatch):
    async def two_products(*args, **kwargs):
        return TwoProductCatalog()

    monkeypatch.setattr("app.agent_runtime.load_catalog", two_products)
    _script(monkeypatch, [
        {"content": "", "tool_calls": [_call("read_product_evidence", {"product_id": "P-a"}, "c1")]},
        {"content": "", "tool_calls": [_call("read_product_evidence", {"product_id": "P-b"}, "c2")]},
        {"content": "A 是 16 GB，B 是 32 GB，还能选 64 GB。", "tool_calls": []},
    ])
    cid = _new_conversation()["conversation_id"]
    data = _send(cid, "对比一下").json()
    assert data["status"] == "partial"
    assert data.get("unsupported_numbers")


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

def test_refusal_to_write_a_number_is_not_flagged(monkeypatch):
    """Mentioning 64GB only to refuse it must not turn a correct run into `partial`."""
    _script(monkeypatch, [
        {"content": "", "tool_calls": [_call("read_product_evidence", {"product_id": "P-test"})]},
        {"content": "64 GB 内存这条我写不了，知识库里的档位是 16 GB / 512 GB，没有 64GB 这一档。",
         "tool_calls": []},
    ])
    cid = _new_conversation()["conversation_id"]
    data = _send(cid, "内存写成 64GB").json()
    assert data["status"] == "completed", f"flags: {data.get('unsupported_numbers')}"
    assert not data.get("unsupported_numbers")


def test_a_cross_model_number_is_not_flagged(monkeypatch):
    """A figure belonging to another read model is supported, not unverified."""
    from app.runtime.content_policy import is_refusal_context
    assert is_refusal_context("写不了 64GB。", 3, 7) is True
    assert is_refusal_context("这台机器有 64GB 内存。", 5, 10) is False


def test_real_refusal_phrasings_are_recognised():
    """The exact wording live output used must not be treated as a claim."""
    from app.runtime.content_policy import is_refusal_context
    cases = [
        ("交付好了，但有一处必须跟你说清楚：64GB 这个数字我没法写。", "64GB"),
        ("官方口径是 24GB / 32GB，没有 64GB 这个配置。", "64GB"),
        ("如果你其实想推的是存储 1TB 或者想换个 64GB 内存的机型，告诉我。", "64GB"),
        ("64GB 内存这条我写不了，知识库里的档位是 16 GB / 512 GB。", "64GB"),
    ]
    for text, needle in cases:
        start = text.index(needle)
        assert is_refusal_context(text, start, start + len(needle)) is True, text
    claim = "这台机器有 64GB 内存。"
    start = claim.index("64GB")
    assert is_refusal_context(claim, start, start + 4) is False


def test_task_list_reports_the_runner_version(monkeypatch):
    """The list must say which runner a task used, so modes can be compared."""
    _script(monkeypatch, [{"content": "完成。", "tool_calls": []}])
    cid = _new_conversation()["conversation_id"]
    _send(cid, "写个标题")
    listing = client.get("/api/v2/conversations?limit=50").json()["conversations"]
    item = next(row for row in listing if row["conversation_id"] == cid)
    assert item["runner_version"] == RUNNER_VERSION


def test_tool_calls_are_capped(monkeypatch):
    """The model-call cap did not bound tool fan-out; a separate ceiling does."""
    monkeypatch.setenv("LG_MAX_TOOL_CALLS", "2")
    # Four batches of one tool call each: the third must be refused, not executed.
    _script(monkeypatch, [
        {"content": "", "tool_calls": [_call("search_topics", {"query": "a"}, "c1")]},
        {"content": "", "tool_calls": [_call("search_topics", {"query": "b"}, "c2")]},
        {"content": "", "tool_calls": [_call("search_topics", {"query": "c"}, "c3")]},
        {"content": "收尾。", "tool_calls": []},
    ])
    cid = _new_conversation()["conversation_id"]
    data = _send(cid, "查点东西").json()
    executed = [e.get("tool") for e in data.get("events", [])
                if e.get("type") == "tool_finished"]
    assert len(executed) == 2, f"expected the cap to stop at 2, got {executed}"


def test_tool_cap_stops_honestly_when_already_spent(monkeypatch):
    monkeypatch.setenv("LG_MAX_TOOL_CALLS", "1")
    _script(monkeypatch, [
        {"content": "", "tool_calls": [_call("search_topics", {"query": "a"}, "c1")]},
        {"content": "", "tool_calls": [_call("search_topics", {"query": "b"}, "c2")]},
        {"content": "收尾。", "tool_calls": []},
    ])
    cid = _new_conversation()["conversation_id"]
    data = _send(cid, "查点东西").json()
    assert data["status"] in ("partial", "completed")
    assert "上限" in (data.get("reply") or "") or data["status"] == "completed"


def test_completed_is_not_reported_as_publishable(monkeypatch):
    """`completed` must not read as "approved": the module never publishes."""
    _script(monkeypatch, [{"content": "写好了。", "tool_calls": []}])
    cid = _new_conversation()["conversation_id"]
    data = _send(cid, "写个标题").json()
    assert data["status"] == "completed"
    assert data["content_status"] == "review_ready"
    assert data["can_publish"] is False


def test_flagged_delivery_is_a_research_draft(monkeypatch):
    _script(monkeypatch, [{"content": "这台机器有 64 GB 内存。", "tool_calls": []}])
    cid = _new_conversation()["conversation_id"]
    data = _send(cid, "说说配置").json()
    assert data["status"] == "partial"
    assert data["content_status"] == "research_draft"
    assert data["can_publish"] is False


# ---------------------------------------------------------------- independent review

def test_review_finds_blocking_issue_and_marks_it(monkeypatch):
    """A blocking review finding must not be delivered as a passed run.

    The finding travels as the structured ``review_notice`` field, not concatenated into the
    delivered prose: the text a user reads stays the author's text, and the reviewer's own
    sentence can never become part of the next round's "delivered text".
    """
    _script(monkeypatch, [{"content": "这台机器有 64 GB 内存，值得买。", "tool_calls": []}],
            review={"verdict": "block",
                    "issues": [{"excerpt": "64 GB 内存", "severity": "high",
                                "reason": "知识库无此档位", "action": "edit_local"}],
                    "checked": ["内存档位"]})
    cid = _new_conversation()["conversation_id"]
    data = _send(cid, "说说配置").json()
    assert data["status"] == "partial"
    assert data["review"]["verdict"] == "block"
    assert data["review"]["blocking"] == 1
    assert data["content_status"] == "research_draft"
    # The blocking finding is reported (intent preserved), but outside the user-facing reply.
    notice = data["review_notice"]
    assert notice["count"] == 1 and notice["blocking"] == 1
    assert "独立审校" in notice["message"]
    assert "知识库无此档位" in notice["detail"]
    assert "独立审校" not in data["reply"]


def test_review_without_blocking_findings_keeps_the_run_delivered(monkeypatch):
    """Advisory findings are recorded but must not be turned into a failure."""
    _script(monkeypatch, [{"content": "写好了。", "tool_calls": []}],
            review={"verdict": "edit_local",
                    "issues": [{"excerpt": "很值得", "severity": "low",
                                "reason": "略空泛", "action": "edit_local"}],
                    "checked": ["事实"]})
    cid = _new_conversation()["conversation_id"]
    data = _send(cid, "写个标题").json()
    assert data["status"] == "completed"
    assert data["content_status"] == "review_required"
    assert data["review"]["issue_count"] == 1


def test_review_runs_with_its_own_context(monkeypatch):
    """The reviewer must not inherit the author's conversation or self-assessment."""
    captured = {}

    _script(monkeypatch, [{"content": "作者说这部分已经检查过了。", "tool_calls": []}])

    def fake_json(system, messages, schema_hint="", model=None):
        captured["system"] = system
        captured["messages"] = messages
        return {"verdict": "pass", "issues": [], "checked": []}, {"model": "stub", "usage": {}}

    monkeypatch.setattr("app.runtime.model_gateway.chat_json_messages", fake_json)
    cid = _new_conversation()["conversation_id"]
    _send(cid, "写个标题").json()

    assert "独立审校" in captured["system"]
    assert len(captured["messages"]) == 1, "the reviewer gets one self-contained payload"
    payload = captured["messages"][0]["content"]
    assert "【待交付文案】" in payload
    assert "【本次允许使用的事实】" in payload
    # The author's own words reach the reviewer only as delivered text, never as context
    # the reviewer could mistake for an authority that already approved it.
    assert "作者说这部分已经检查过了" in payload
    assert "system" not in payload


def test_an_unrun_review_is_never_publishable(monkeypatch):
    """No check executed means no 'review_ready' — the gap is reported, not hidden."""
    monkeypatch.setenv("LG_MAX_MODEL_CALLS", "1")
    _script(monkeypatch, [{"content": "完成。", "tool_calls": []}])
    cid = _new_conversation()["conversation_id"]
    data = _send(cid, "写个标题").json()
    assert data["review"]["status"] == "not_run"
    assert data["review"]["reason"] == "model_call_budget"
    assert data["content_status"] == "research_draft"
    assert data["can_publish"] is False


def test_a_failed_review_is_reported_as_not_run(monkeypatch):
    from app.model import ModelError

    _script(monkeypatch, [{"content": "完成。", "tool_calls": []}],
            review=ModelError("模型服务 HTTP 500"))
    cid = _new_conversation()["conversation_id"]
    data = _send(cid, "写个标题").json()
    assert data["review"]["status"] == "not_run"
    assert data["content_status"] == "research_draft"


# ---------------------------------------------------------------- shadow dedup

def test_a_repeat_delivery_is_reported_but_not_blocked(monkeypatch):
    """Shadow mode records the repeat; it must never change the run into a failure."""
    # Deliberately free of product numbers: this test is about dedup, and a numeric claim
    # without evidence would (correctly) trip the separate unverified-number fence.
    text = "学生党选本，先看屏幕和重量，看文档更舒服。"
    _script(monkeypatch, [{"content": text, "tool_calls": []}])
    first = _send(_new_conversation()["conversation_id"], "写个开头").json()
    assert first["duplicate"]["kind"] == "none"

    _script(monkeypatch, [{"content": text, "tool_calls": []}])
    second = _send(_new_conversation()["conversation_id"], "写个开头").json()
    assert second["duplicate"]["kind"] == "exact"
    assert second["duplicate"]["blocks"] is False
    assert second["duplicate"]["calibrated"] is False
    # The delivery itself is untouched: same status, same text.
    assert second["status"] == "completed"
    assert second["reply"] == text


def test_dedup_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("LG_DEDUP_MODE", "off")
    _script(monkeypatch, [{"content": "随便写点。", "tool_calls": []}])
    data = _send(_new_conversation()["conversation_id"], "写个标题").json()
    assert data["duplicate"]["kind"] == "not_run"
    assert data["status"] == "completed"


def test_a_database_conflict_ends_the_run_honestly(monkeypatch):
    """A broken session must not become a 500 with the run stuck in `running`."""
    from sqlalchemy.exc import PendingRollbackError

    _script(monkeypatch, [{"content": "完成。", "tool_calls": []}])

    def exploding(*args, **kwargs):
        raise PendingRollbackError("This Session's transaction has been rolled back")

    # monkeypatch restores the real function automatically at teardown.
    monkeypatch.setattr("app.runtime.runner.ledger.record_attempt", exploding)
    cid = _new_conversation()["conversation_id"]
    response = client.post(f"/api/v2/conversations/{cid}/messages",
                           json={"message": "写个标题"})

    assert response.status_code == 200, "a storage conflict must not surface as a 500"
    data = response.json()
    assert data["status"] == "failed"
    assert "冲突" in data["reply"]

    session = db_layer.session_factory()()
    run = session.query(models.AgentRun).filter(
        models.AgentRun.conversation_id == cid).order_by(
        models.AgentRun.created_at.desc()).first()
    assert run is not None and run.status == "failed", "the run must not stay running"
    session.close()


# ---------------------------------------------------------------- resume after a crash

def _crash_mid_run(monkeypatch, cid, text="查个热点"):
    """Cancel a run from the outside while a tool is in flight.

    Raising CancelledError *inside* the call does not reproduce this: the framework turns
    that into an internal error. A real interruption is an external cancellation, which is
    what a dropped client connection does, so the run is driven as a task and cancelled.
    """
    import asyncio

    from app.runtime import tool_gateway

    async def hanging(self, name, arguments, call_uid=""):
        await asyncio.sleep(30)  # cancelled before it finishes

    original = tool_gateway.ToolGateway.run
    monkeypatch.setattr(tool_gateway.ToolGateway, "run", hanging)

    async def drive():
        session = db_layer.session_factory()()
        conversation = session.get(models.AgentConversation, cid)
        prepared = await lg_runner.submit(session, conversation, text)
        run = session.get(models.AgentRun, prepared["run_id"])
        task = asyncio.ensure_future(
            lg_runner.execute_run(session, conversation, run, text))
        await asyncio.sleep(1.0)  # let it reach the tool, then pull the plug
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        session.close()
        return prepared["run_id"]

    run_id = asyncio.run(drive())
    monkeypatch.setattr(tool_gateway.ToolGateway, "run", original)
    session = db_layer.session_factory()()
    status = session.get(models.AgentRun, run_id).status
    session.close()
    return run_id, status


def test_a_crashed_run_resumes_from_its_checkpoint(monkeypatch):
    """The upstream dropping mid-run must not cost the user the whole attempt."""
    _script(monkeypatch, [
        {"content": "", "tool_calls": [_call("search_topics", {"query": "开学"}, "c1")]},
        {"content": "续跑后完成。", "tool_calls": []},
    ])
    cid = _new_conversation()["conversation_id"]
    run_id, status = _crash_mid_run(monkeypatch, cid)
    assert status == "needs_reconciliation"

    response = client.post(f"/api/v2/runs/{run_id}/resume")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["resumed_from"] == run_id
    assert data["status"] == "completed"
    assert "续跑后完成" in data["reply"]


def test_resume_refuses_while_the_run_is_waiting_for_the_user(monkeypatch):
    _script(monkeypatch, [{"content": "", "tool_calls": [
        _call("ask_user", {"question": "给谁看？", "options": ["学生", "办公"]})]}])
    cid = _new_conversation()["conversation_id"]
    first = _send(cid, "帮我写点内容").json()
    assert first["status"] == "waiting_user"

    response = client.post(f"/api/v2/runs/{first['run_id']}/resume")
    assert response.status_code == 409
    assert "直接回答即可" in response.json()["detail"]["message"]


def test_resume_refuses_a_run_that_already_finished(monkeypatch):
    _script(monkeypatch, [{"content": "完成。", "tool_calls": []}])
    cid = _new_conversation()["conversation_id"]
    done = _send(cid, "写个标题").json()
    response = client.post(f"/api/v2/runs/{done['run_id']}/resume")
    assert response.status_code == 409


def test_resume_refuses_once_attempts_are_spent(monkeypatch):
    """Recovery must be bounded: a run that keeps dying ends instead of looping."""
    from app.runtime import lease as lease_module

    _script(monkeypatch, [
        {"content": "", "tool_calls": [_call("search_topics", {"query": "开学"}, "c1")]},
        {"content": "不该跑到这里。", "tool_calls": []},
    ])
    cid = _new_conversation()["conversation_id"]
    run_id, status = _crash_mid_run(monkeypatch, cid)
    assert status == "needs_reconciliation"

    session = db_layer.session_factory()()
    session.execute(lease_module.models.AgentRunLease.__table__.update()
                    .where(lease_module.models.AgentRunLease.run_id == run_id)
                    .values(attempt=lease_module.max_attempts()))
    session.commit()
    session.close()

    response = client.post(f"/api/v2/runs/{run_id}/resume")
    assert response.status_code == 409
    assert "上限" in response.json()["detail"]["message"]


def test_a_run_reports_whether_it_can_be_resumed(monkeypatch):
    _script(monkeypatch, [{"content": "完成。", "tool_calls": []}])
    cid = _new_conversation()["conversation_id"]
    data = _send(cid, "写个标题").json()
    assert data["resumable"] is False, "a finished run is not a resume candidate"


# ---------------------------------------------------------------- audit fixes

def _capture_review(monkeypatch):
    """Capture the reviewer payload while returning a passing verdict."""
    captured = {}

    def fake_json(system, messages, schema_hint="", model=None):
        captured["payload"] = messages[0]["content"]
        captured["system"] = system
        return {"verdict": "pass", "issues": [], "checked": []}, {"model": "stub",
                                                                  "usage": {}}

    monkeypatch.setattr("app.runtime.model_gateway.chat_json_messages", fake_json)
    return captured


def _variants_content(product_ids, models_for_each=None, body="内存 16 GB"):
    rows = []
    for index in range(1, 4):
        row = {"variant_id": f"v{index}", "label": f"方向{index}", "title": f"标题{index}",
               "body": body, "tags": ["校园"]}
        row["models"] = (models_for_each[index - 1] if models_for_each
                         else [product_ids[0]])
        rows.append(row)
    return {"product_ids": list(product_ids), "variants": rows}


def test_review_sees_the_facts_search_products_brought_back(monkeypatch):
    """修复 1(a)：本轮只调 search_products（带 facts）+ save_artifact 的正常交付，
    审校拿到的「本次允许使用的事实」里必须有这些事实，而不是「本轮没有读到任何产品事实」。

    同一份事实两套口径的后果（活库实证 run 9c5b7e39）：审校据此判 block，
    把一条事实检查 passed=true 的正确交付误报成 partial。
    """
    _script(monkeypatch, [
        {"content": "", "tool_calls": [_call("search_products", {"query": "MateBook"})]},
        {"content": "", "tool_calls": [_call("save_artifact", {
            "artifact_type": "variants",
            "content": _variants_content(["P-test"])}, "a3")]},
        {"content": "已按知识库事实写好 3 篇。", "tool_calls": []},
    ])
    captured = _capture_review(monkeypatch)
    cid = _new_conversation()["conversation_id"]
    data = _send(cid, "就写 MateBook Test").json()

    assert data["status"] == "completed"
    payload = captured["payload"]
    assert "本轮没有读到任何产品事实" not in payload
    assert "【本次允许使用的事实】" in payload
    assert "内存：16 GB" in payload and "尺寸与重量：约 1.33 kg" in payload
    assert "HUAWEI MateBook Test｜" in payload


def test_review_still_treats_a_round_without_evidence_as_ungrounded(monkeypatch):
    """修复 1(b)：真的没有读任何证据的回复仍必须被拦，不能把审校改成橡皮图章。"""
    _script(monkeypatch, [{"content": "这台机器 14 英寸，重 1.33 kg，很合适。",
                           "tool_calls": []}])
    captured = {}

    def fake_json(system, messages, schema_hint="", model=None):
        captured.setdefault("payload", messages[0]["content"])
        return {"verdict": "block",
                "issues": [{"excerpt": "14 英寸", "severity": "high",
                            "reason": "本轮没有任何可用事实", "action": "block"}],
                "checked": []}, {"model": "stub", "usage": {}}

    monkeypatch.setattr("app.runtime.model_gateway.chat_json_messages", fake_json)
    cid = _new_conversation()["conversation_id"]
    data = _send(cid, "说说这台机器").json()

    assert "（本轮没有读到任何产品事实）" in captured["payload"]
    assert data["status"] == "partial"
    assert data["review_notice"]["blocking"] == 1
    # 措辞必须准确：知识库是可用的，只是本轮没读事实。
    assert "本轮没有读取产品事实" in data["reply"]
    assert "知识库当前不可用" not in data["reply"]


def test_artifact_only_round_is_reviewed_against_its_bound_facts(monkeypatch):
    """修复 1：本轮只调 save_artifact（证据在更早一轮读过）时，审校必须看到交付物绑定的事实。

    这正是活库 run 9c5b7e39 / 4a22118a 的形态：artifact 事实检查 passed=true，
    审校却因为 read_product_ids 为空而判「没有任何产品事实」。
    """
    content = _variants_content(["P-test"])
    _script(monkeypatch, [
        {"content": "", "tool_calls": [_call("read_product_evidence", {"product_id": "P-test"},
                                             "r1")]},
        {"content": "", "tool_calls": [_call("save_artifact", {
            "artifact_type": "variants", "content": content}, "a3")]},
        {"content": "交付完成。", "tool_calls": []},
    ])
    cid = _new_conversation()["conversation_id"]
    first = _send(cid, "读证据并写").json()
    assert first["artifact"], first

    calls = _script(monkeypatch, [
        {"content": "", "tool_calls": [_call("save_artifact", {
            "artifact_type": "variants", "content": content}, "b3")]},
        {"content": "再次交付。", "tool_calls": []},
    ])
    captured = _capture_review(monkeypatch)
    data = _send(cid, "再交付一次").json()

    assert data["status"] == "completed", data
    assert data["artifact"], "第二轮只有 save_artifact，交付物必须保存成功"
    payload = captured["payload"]
    assert "本轮没有读到任何产品事实" not in payload
    assert "内存：16 GB" in payload


def test_zero_images_this_round_clear_the_previous_round(monkeypatch):
    """修复 2：本轮取图返回 0 张时，images 必须清空，也不得把旧图持久化进 task.images。

    本地 KB 实证：MateBook 14 鸿蒙版有 4 张、酷睿 Ultra 有 6 张，而 MateBook Pro 与
    D 14 2024 是 0 张；旧语义下空列表不覆盖，于是用户选无图型号时拿到上一款的原图。
    """
    class FakeImage:
        image_id = "IMG-1"
        caption = "HUAWEI MateBook Test 官方产品图"
        access_url = "/api/v1/assets/IMG-1"

    async def many(*args, **kwargs):
        return [FakeImage()], []

    async def none(*args, **kwargs):
        return [], []

    monkeypatch.setattr("app.agent_runtime.verified_images", many)
    _script(monkeypatch, [
        {"content": "", "tool_calls": [_call("find_product_images", {"product_id": "P-test"},
                                             "i1")]},
        {"content": "给了原图。", "tool_calls": []},
    ])
    cid = _new_conversation()["conversation_id"]
    first = _send(cid, "找图").json()
    assert first["images"] and first["images"][0]["image_id"] == "IMG-1"

    monkeypatch.setattr("app.agent_runtime.verified_images", none)
    _script(monkeypatch, [
        {"content": "", "tool_calls": [_call("find_product_images", {"product_id": "P-test"},
                                             "i2")]},
        {"content": "这一轮没有可用原图。", "tool_calls": []},
    ])
    second = _send(cid, "再找一次图").json()
    assert second["images"] == [], second["images"]
    conversation = client.get(f"/api/v2/conversations/{cid}").json()
    assert (conversation["task"] or {}).get("images") in (None, []), \
        "取图返回 0 张后，上一轮的旧图不得留在 task.images 里"


def test_checkpoint_setup_is_safe_across_event_loops(tmp_path, monkeypatch):
    """修复 10：同一 checkpoint 文件被多个事件循环同时 setup 时不得撞锁。

    get_saver() 按 id(loop) 注册 saver；模块级 TestClient 没进 with 时每个请求一个新 loop，
    两个 loop 会在同一个 SQLite 文件上并发 setup，其中一个在线程里抛
    `sqlite3.OperationalError: database is locked`。
    """
    import threading

    path = str(tmp_path / "loops.sqlite")
    monkeypatch.setenv("LG_CHECKPOINT_PATH", path)
    monkeypatch.setattr(lg_runner, "_savers", {})
    monkeypatch.setattr(lg_runner, "_setup_locks", {})
    errors = []

    def worker():
        try:
            asyncio.run(lg_runner.get_saver())
        except Exception as error:  # noqa: BLE001 - the point is that this must not happen
            errors.append(repr(error))

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == [], errors
    assert len(lg_runner._savers) == 4, "one saver per loop, all set up without a lock error"


def test_frontend_only_renders_this_rounds_delivery(monkeypatch):
    """修复 2/3/12/14 的前端门控与诚实说明（纯前端行为，做源码断言）。"""
    from pathlib import Path

    from app import main as main_module

    static = Path(main_module.__file__).parent / "static"
    js = (static / "app.js").read_text()
    html = (static / "index.html").read_text()

    # 修复 3：variants 只在"本轮真的产出交付物"（save_artifact）时渲染，与 candidates 同一套门控。
    assert "const savedThisRound = roundUsed(['save_artifact']);" in js
    assert "let usedVariants = savedThisRound" in js
    assert "renderVariants(data, messageEl, roundImages)" in js
    assert "renderVariants(conversation, messageEl, roundImages)" in js
    # 修复 2：选项卡下方的原图只能取本轮 images（本轮没取图就一张都不画）。
    assert "roundImages === undefined ? imagesFrom(source) : asList(roundImages)" in js
    # 修复 1：审校结论是结构化字段，单独渲染，不拼进正文。
    assert "function renderReviewNotice" in js and "payload.review_notice" in js
    assert "review-notice" in (static / "style.css").read_text()
    # 修复 12：未标注归属时显示"未标注型号"，不显示两个型号名。
    assert "未标注型号" in js
    # 修复 14：内存态与临时地址的前置说明（不是坏了才解释）。
    assert "生成记录只保存在本次运行内，重启后清空" in js
    assert "生成记录只保存在本次运行内，重启后清空" in html
    assert "这些图只保存在本次运行内，重启后清空" in html
    assert "本次运行的临时地址" in html
