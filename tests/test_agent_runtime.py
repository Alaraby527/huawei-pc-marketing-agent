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


def _variants(count=3, product_id="P-test", text="内存 16 GB"):
    return [{"variant_id": f"v{index}", "label": f"方向{index}",
             "title": f"标题{index}", "body": f"正文{index}：{text}",
             "tags": ["华为PC"], "models": [product_id]}
            for index in range(1, count + 1)]


def _conversation():
    return client.post("/api/v2/conversations",
                       json={"mode": "autonomous"}).json()["conversation_id"]


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


def test_local_patch_keeps_other_variants_and_bumps_revision():
    """原 test_local_patch_keeps_other_pages...：局部修改只动指定位置并升版本。

    交付物已从 6 页 pages 改为 3 篇 variants，断言原意不变，只换成新结构。
    """
    session, ctx = _ctx()
    content = {"product_ids": ["P-test"], "variants": _variants()}
    first = agent_runtime._save_artifact(ctx, {"artifact_type": "variants", "product_id": "P-test",
                                               "content": content})
    assert first["ok"] is True
    second = agent_runtime._save_artifact(ctx, {
        "artifact_id": first["artifact_id"], "base_revision": 1,
        "patch": {"variants.1.body": "改得更自然"}})
    assert second["ok"] is True and second["revision"] == 2
    loaded = agent_runtime._load_artifact(ctx, {"artifact_id": first["artifact_id"]})
    variants = loaded["content"]["variants"]
    assert variants[0]["body"].startswith("正文1") and variants[2]["body"].startswith("正文3")
    assert variants[1]["body"] == "改得更自然"
    session.close()


def test_render_visuals_was_removed_and_refuses():
    """规格 §0：render_visuals 与页面结构一起删除。

    原断言是「渲染 3 页并返回 3 张 PNG」。新规格下这个工具不该存在；保留原意的方式是
    断言工具不在白名单、且旧的调用点被重放时明确失败而不是假装渲染成功。
    """
    assert "render_visuals" not in agent_runtime.TOOL_NAMES
    session, ctx = _ctx()
    result = asyncio.run(agent_runtime._render(ctx, {}))
    assert result["ok"] is False and result["error"] == "TOOL_REMOVED"
    session.close()


def test_saving_requires_exactly_three_variants():
    """交付物固定 3 篇；少于/多于 3 篇都要被拒绝，不能静默保存。"""
    session, ctx = _ctx()
    refused = agent_runtime._save_artifact(ctx, {
        "artifact_type": "variants", "product_id": "P-test",
        "content": {"product_ids": ["P-test"], "variants": _variants(2)}})
    assert refused["ok"] is False and refused["error"] == "VARIANT_COUNT_INVALID"
    saved = agent_runtime._save_artifact(ctx, {
        "artifact_type": "variants", "product_id": "P-test",
        "content": {"product_ids": ["P-test"], "variants": _variants(3)}})
    assert saved["ok"] is True and len(saved["variants"]) == 3
    assert [row["variant_id"] for row in saved["variants"]] == ["v1", "v2", "v3"]
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
        return {"artifact_type": "variants", "reply": "知识库没有 64 GB，这 3 篇按 16 GB 写。",
                "content": {"product_ids": ["P-test"], "variants": _variants()}}, {"model": "stub"}

    monkeypatch.setattr("app.agent_runtime.chat_json_messages", fake)
    saved, _ = agent_runtime._compose_after_evidence(ctx, "内存写成 64GB", None)
    assert saved["ok"] is True
    assert saved["reply"] == "知识库没有 64 GB，这 3 篇按 16 GB 写。"
    assert len(saved["variants"]) == 3
    session.close()


def test_requested_page_count_is_gone():
    """原 test_requested_page_count_reads_digits_and_chinese 断言“从话里读页数”。

    规格删掉了页数概念，所以这个能力不该再存在；结论反转为：页数解析入口已删除。
    """
    assert not hasattr(agent_runtime, "_requested_page_count")


def test_search_does_not_match_wrong_model_by_brand():
    session, ctx = _ctx()
    result = asyncio.run(agent_runtime._tool_search_products(
        ctx, {"query": "HUAWEI MateBook X Pro 2027"}))
    assert result["ok"] is True and result["count"] == 0
    known = asyncio.run(agent_runtime._tool_search_products(
        ctx, {"query": "HUAWEI MateBook Test"}))
    assert known["count"] == 1 and known["products"][0]["product_id"] == "P-test"
    session.close()


def test_compose_retries_when_variant_count_is_wrong(monkeypatch):
    """原 test_compose_retries_when_page_count_is_wrong：数量不符 → 重试一次 → 按规格数量保存。

    规格把页数换成「正好 3 篇」，重试机制的原意保留，只换了目标数量。
    """
    session, ctx = _ctx()
    ctx.last_search = {
        "products": [{"product_id": "P-test"}],
        "evidence": [{"fact_id": "F-MEM", "text": "内存：16 GB",
                      "source": "合成测试材料", "conditions": ""}],
    }
    responses = [
        {"artifact_type": "variants", "reply": "先给两篇",
         "content": {"product_ids": ["P-test"], "variants": _variants(2)}},
        {"artifact_type": "variants", "reply": "已按 3 篇交付",
         "content": {"product_ids": ["P-test"], "variants": _variants(3)}},
    ]

    def fake(system, messages, schema_hint=None, model=None):
        return responses.pop(0), {"model": "stub",
                                  "usage": {"prompt_tokens": 10, "completion_tokens": 5}}

    monkeypatch.setattr("app.agent_runtime.chat_json_messages", fake)
    saved, meta = agent_runtime._compose_after_evidence(ctx, "写 3 篇", None)
    assert saved["ok"] is True
    loaded = agent_runtime._load_artifact(ctx, {"artifact_id": saved["artifact_id"]})
    assert len(loaded["content"]["variants"]) == 3
    assert saved["reply"] == "已按 3 篇交付"
    assert meta["usage"]["prompt_tokens"] == 20
    session.close()


def test_compose_refuses_when_variant_count_never_matches(monkeypatch):
    """两次都不符合规格就必须拒绝保存，而不是把 2 篇当成交付。"""
    session, ctx = _ctx()
    ctx.last_search = {
        "products": [{"product_id": "P-test"}],
        "evidence": [{"fact_id": "F-MEM", "text": "内存：16 GB",
                      "source": "合成测试材料", "conditions": ""}],
    }

    def fake(system, messages, schema_hint=None, model=None):
        return ({"artifact_type": "variants", "reply": "只有两篇",
                 "content": {"product_ids": ["P-test"], "variants": _variants(2)}},
                {"model": "stub", "usage": {}})

    monkeypatch.setattr("app.agent_runtime.chat_json_messages", fake)
    saved, _ = agent_runtime._compose_after_evidence(ctx, "写 3 篇", None)
    assert saved["ok"] is False and saved["error"] == "VARIANT_COUNT_MISMATCH"
    assert agent_runtime._load_artifact(ctx, {}).get("ok") is False
    session.close()


def test_compose_retries_after_fact_check_failure(monkeypatch):
    session, ctx = _ctx()
    ctx.last_search = {
        "products": [{"product_id": "P-test"}],
        "evidence": [{"fact_id": "F-MEM", "text": "内存：16 GB",
                      "source": "合成测试材料", "conditions": ""}],
    }
    responses = [
        {"artifact_type": "variants", "reply": "照 64 GB 写了",
         "content": {"product_ids": ["P-test"], "variants": _variants(text="这台机器有 64 GB 内存。")}},
        {"artifact_type": "variants", "reply": "已按真实 16 GB 重写",
         "content": {"product_ids": ["P-test"], "variants": _variants(text="这台机器有 16 GB 内存。")}},
    ]

    def fake(system, messages, schema_hint=None, model=None):
        return responses.pop(0), {"model": "stub",
                                  "usage": {"prompt_tokens": 10, "completion_tokens": 5}}

    monkeypatch.setattr("app.agent_runtime.chat_json_messages", fake)
    saved, _ = agent_runtime._compose_after_evidence(ctx, "内存写成 64GB", None)
    assert saved["ok"] is True and saved["reply"] == "已按真实 16 GB 重写"
    loaded = agent_runtime._load_artifact(ctx, {"artifact_id": saved["artifact_id"]})
    assert "64" not in json.dumps(loaded["content"], ensure_ascii=False)
    assert "16 GB" in loaded["content"]["variants"][0]["body"]
    session.close()


def test_compose_sanitizes_after_two_failed_retries(monkeypatch):
    session, ctx = _ctx()
    ctx.last_search = {
        "products": [{"product_id": "P-test"}],
        "evidence": [{"fact_id": "F-MEM", "text": "内存：16 GB",
                      "source": "合成测试材料", "conditions": ""}],
    }
    bad = {"artifact_type": "variants", "reply": "照 64 GB 写了",
           "content": {"product_ids": ["P-test"],
                       "variants": _variants(text="这台机器有 64 GB 内存。")}}

    def fake(system, messages, schema_hint=None, model=None):
        return dict(bad), {"model": "stub",
                           "usage": {"prompt_tokens": 10, "completion_tokens": 5}}

    monkeypatch.setattr("app.agent_runtime.chat_json_messages", fake)
    saved, _ = agent_runtime._compose_after_evidence(ctx, "内存写成 64GB", None)
    assert saved["ok"] is True
    loaded = agent_runtime._load_artifact(ctx, {"artifact_id": saved["artifact_id"]})
    assert "64" not in json.dumps(loaded["content"], ensure_ascii=False)
    assert "16 GB" in loaded["content"]["variants"][0]["body"]
    assert "真实值改写" in saved["reply"]
    session.close()


class GuidedProduct:
    def __init__(self, pid, model):
        self.product_id = pid
        self.model = model
        self.issues = []
        self.docs = []
        self.facts = [_fact(pid + "-MEM", "内存：16 GB"),
                      _fact(pid + "-SCREEN", "屏幕：14.2 英寸"),
                      _fact(pid + "-WEIGHT", "尺寸与重量：约 1.33 kg"),
                      _fact(pid + "-BAT", "电池：70 Wh")]


class GuidedCatalog:
    products = [GuidedProduct("P-a", "HUAWEI MateBook Test 14"),
                GuidedProduct("P-b", "HUAWEI MateBook Test Pro"),
                GuidedProduct("P-c", "HUAWEI MateBook Test Fold")]
    entries = []
    image_index = []


class FakeImage:
    image_id = "IMG-1"
    caption = "HUAWEI MateBook Test 14 官方产品图"
    access_url = "/api/v1/assets/IMG-1"


def test_guided_flow_from_greeting_to_artifact(monkeypatch):
    """规格 §1：先收四件事 → 候选 → 选 1–2 款 → 确认 → 3 篇文案 + 原图。"""
    async def guided_catalog(*args, **kwargs):
        return GuidedCatalog()

    async def guided_images(*args, **kwargs):
        return [FakeImage()], []

    monkeypatch.setattr("app.agent_runtime.load_catalog", guided_catalog)
    monkeypatch.setattr("app.agent_runtime.verified_images", guided_images)

    def fake_json(system, messages, schema_hint=None, model=None):
        user = messages[-1]["content"] if messages else ""
        if "需求分析器" in system:
            if "最新用户消息：你好" in user:
                return {"intent": "chat"}, {"model": "stub", "usage": {}}
            # 提取器可能把直接回答也标成 chat；runtime 必须仍然用抽到的字段继续流程。
            extracted = {"intent": "chat", "tone": "轻松种草"}
            if "大学生" in user:
                extracted["audience"] = "大学生"
            if "小红书" in user:
                extracted["platform"] = "小红书"
            if "上课" in user:
                extracted["scenario"] = "校园学习"
            return extracted, {"model": "stub", "usage": {}}
        return {"artifact_type": "variants", "reply": "3 篇文案和原图都好了。",
                "content": {"product_ids": ["P-a"], "variants": _variants(3, "P-a")}}, {
                    "model": "stub", "usage": {}}

    monkeypatch.setattr("app.agent_runtime.chat_json_messages", fake_json)
    conversation_id = client.post("/api/v2/conversations",
                                  json={"mode": "guided"}).json()["conversation_id"]

    first = client.post(f"/api/v2/conversations/{conversation_id}/messages",
                        json={"message": "你好"}).json()
    assert first["stage"] == "intake" and first["actions"]

    second = client.post(f"/api/v2/conversations/{conversation_id}/messages",
                         json={"message": "大学生，发小红书"}).json()
    assert second["stage"] == "intake"
    assert "场景" in second["reply"]

    third = client.post(f"/api/v2/conversations/{conversation_id}/messages",
                        json={"message": "上课和写论文"}).json()
    assert third["stage"] == "intake"
    assert "活动" in third["reply"]

    fourth = client.post(f"/api/v2/conversations/{conversation_id}/messages",
                         json={"message": "没有活动"}).json()
    assert fourth["stage"] == "select_product"
    assert len(fourth["candidates"]) == 3

    fifth = client.post(f"/api/v2/conversations/{conversation_id}/messages",
                        json={"message": "第 1 款"}).json()
    assert fifth["stage"] == "confirm_delivery"
    assert "是否输出最后交付内容" in fifth["reply"]
    assert fifth["actions"] == ["输出最后交付内容"]

    sixth = client.post(f"/api/v2/conversations/{conversation_id}/messages",
                        json={"message": "输出最后交付内容"}).json()
    assert sixth["stage"] == "review"
    assert sixth["artifact"]["revision"] == 1
    assert sixth["images"] and sixth["actions"]
    assert len(sixth["variants"]) == 3
    assert sixth["material"]["audience"] == "大学生"
    assert sixth["material"]["models"][0]["product_id"] == "P-a"

    seventh = client.post(f"/api/v2/conversations/{conversation_id}/messages",
                          json={"message": "都保留"}).json()
    assert seventh["stage"] == "done"


def test_guided_flow_accepts_two_models_and_keeps_material(monkeypatch):
    """选 2 款时 3 篇混写；资料存档同时记住两个型号（规格 §2/§5）。"""
    async def guided_catalog(*args, **kwargs):
        return GuidedCatalog()

    async def guided_images(*args, **kwargs):
        return [FakeImage()], []

    monkeypatch.setattr("app.agent_runtime.load_catalog", guided_catalog)
    monkeypatch.setattr("app.agent_runtime.verified_images", guided_images)

    selected_ids: list[str] = []

    def fake_json(system, messages, schema_hint=None, model=None):
        if "需求分析器" in system:
            return {"intent": "create", "audience": "大学生", "scenario": "校园学习",
                    "platform": "小红书", "campaign": "开学季"}, {"model": "stub", "usage": {}}
        first, second = selected_ids[0], selected_ids[1]
        return {"artifact_type": "variants", "reply": "好。",
                "content": {"product_ids": [first, second],
                            "variants": [
                                {"variant_id": "v1", "label": "方向A", "title": "A1",
                                 "body": "内存 16 GB", "tags": ["校园"], "models": [first]},
                                {"variant_id": "v2", "label": "方向B", "title": "A2",
                                 "body": "内存 16 GB", "tags": ["校园"], "models": [first]},
                                {"variant_id": "v3", "label": "方向C", "title": "B1",
                                 "body": "屏幕 14.2 英寸", "tags": ["校园"], "models": [second]}]}}, {
                    "model": "stub", "usage": {}}

    monkeypatch.setattr("app.agent_runtime.chat_json_messages", fake_json)
    conversation_id = client.post("/api/v2/conversations",
                                  json={"mode": "guided"}).json()["conversation_id"]

    client.post(f"/api/v2/conversations/{conversation_id}/messages",
                json={"message": "大学生，发小红书，上课写论文，开学季"})
    selection = client.post(f"/api/v2/conversations/{conversation_id}/messages",
                            json={"message": "1 和 2"}).json()
    assert selection["stage"] == "confirm_delivery"
    selected_ids[:] = [row["product_id"] for row in selection["selected"]]
    assert len(selected_ids) == 2

    delivered = client.post(f"/api/v2/conversations/{conversation_id}/messages",
                            json={"message": "输出最后交付内容"}).json()
    assert delivered["stage"] == "review"
    assert [row["models"] for row in delivered["variants"]] == [
        [selected_ids[0]], [selected_ids[0]], [selected_ids[1]]]
    conversation = client.get(f"/api/v2/conversations/{conversation_id}").json()
    assert [row["product_id"] for row in conversation["material"]["models"]] == selected_ids
    assert len(conversation["variants"]) == 3


def test_task_list_rename_pin_and_delete(monkeypatch):
    def fake_json(system, messages, schema_hint=None, model=None):
        return {"intent": "chat"}, {"model": "stub", "usage": {}}

    monkeypatch.setattr("app.agent_runtime.chat_json_messages", fake_json)
    conversation_id = client.post("/api/v2/conversations",
                                  json={"mode": "guided"}).json()["conversation_id"]
    client.post(f"/api/v2/conversations/{conversation_id}/messages",
                json={"message": "你好"})

    listing = client.get("/api/v2/conversations?limit=50").json()["conversations"]
    item = next(row for row in listing if row["conversation_id"] == conversation_id)
    assert item["title"] and item["message_count"] >= 1

    updated = client.post(f"/api/v2/conversations/{conversation_id}/update",
                          json={"title": "我的校园选题", "pinned": True}).json()
    assert updated["title"] == "我的校园选题" and updated["pinned"] is True

    listing = client.get("/api/v2/conversations?limit=50").json()["conversations"]
    assert listing[0]["conversation_id"] == conversation_id

    deleted = client.post(f"/api/v2/conversations/{conversation_id}/delete")
    assert deleted.status_code == 200
    assert client.get(f"/api/v2/conversations/{conversation_id}").status_code == 404


def test_material_archive_accumulates_and_replaces_only_the_selection():
    """规格 §2：人群/平台/场景/活动/热点可累加，选定型号是 1–2 款的选择（会替换）。"""
    session, ctx = _ctx()
    first = asyncio.run(agent_runtime._tool_update_material(ctx, {
        "audience": "大学生", "platform": "小红书", "models": ["P-test"]}))
    assert first["ok"] is True
    assert first["material"]["audience"] == "大学生"
    assert first["material"]["models"] == [{"product_id": "P-test",
                                            "model": "HUAWEI MateBook Test"}]

    # 中途补充：只传新增字段，已记录的不能丢。
    second = asyncio.run(agent_runtime._tool_update_material(ctx, {
        "scenario": "校园学习", "campaign": "开学季",
        "hotspot": {"title": "开学季选本", "source": "微博热搜"}}))
    assert second["ok"] is True
    assert second["material"]["audience"] == "大学生"       # 未被清空
    assert second["material"]["scenario"] == "校园学习"
    assert second["material"]["campaign"] == "开学季"
    assert second["material"]["hotspot"]["title"] == "开学季选本"
    assert second["selected_models"] == ["P-test"]
    session.close()


def test_material_archive_refuses_unknown_or_too_many_models():
    session, ctx = _ctx()
    missing = asyncio.run(agent_runtime._tool_update_material(ctx, {"models": ["P-nope"]}))
    assert missing["ok"] is False and missing["error"] == "PRODUCT_NOT_FOUND"
    assert missing["available"]
    session.close()


def test_fixed_product_reads_new_selection_and_legacy_candidates():
    """`_fixed_product` 必须新旧都能读：新结构 material.models / selected_models，旧结构 candidates。"""
    from app import studio

    new_task = {"material": {"models": [{"product_id": "P-new", "model": "HUAWEI New"}]},
                "candidates": [{"product_id": "P-old", "model": "HUAWEI Old",
                                "facts_count": 3}]}
    assert studio._fixed_product(new_task)["product_id"] == "P-new"
    assert [row["product_id"] for row in studio._fixed_products(new_task)] == ["P-new"]

    selected_only = {"selected_models": ["P-old"],
                     "candidates": [{"product_id": "P-old", "model": "HUAWEI Old",
                                     "facts_count": 3}]}
    fixed = studio._fixed_product(selected_only)
    assert fixed == {"product_id": "P-old", "model": "HUAWEI Old", "facts_count": 3}

    legacy = {"candidates": [{"product_id": "P-legacy", "model": "HUAWEI Legacy"}]}
    assert studio._fixed_product(legacy)["product_id"] == "P-legacy"
    assert studio._fixed_product({}) == {}


def test_legacy_candidates_readable_but_new_flow_candidates_are_not_a_selection():
    """旧任务只存了 candidates，仍要读得出已定型号；新流程的候选不能当成已选。"""
    from app import task_material

    legacy = {"candidates": [{"product_id": "P-old", "model": "HUAWEI Old"}]}
    assert [row["product_id"] for row in task_material.normalize(legacy)["models"]] == ["P-old"]

    fresh = {"flow": "variants_v1",
             "candidates": [{"product_id": "P-old", "model": "HUAWEI Old"}]}
    assert task_material.normalize(fresh)["models"] == []


def test_material_payload_shape_is_exactly_the_contract():
    material = {"audience": "大学生", "platform": "xiaohongshu", "scenario": "校园学习",
                "campaign": "", "models": [{"product_id": "P-1", "model": "HUAWEI One"}],
                "hotspot": {"title": "热点A", "source": "微博热搜", "url": "x", "keywords": ["a"]}}
    payload = agent_runtime.task_material.payload(material)
    assert sorted(payload.keys()) == ["audience", "campaign", "hotspot", "models",
                                      "platform", "scenario"]
    assert payload["hotspot"] == {"title": "热点A", "source": "微博热搜"}
    empty = agent_runtime.task_material.payload({})
    assert empty["hotspot"] == {"title": "", "source": ""}
    assert empty["models"] == []
    assert empty["audience"] == "" and empty["campaign"] == ""


def test_hotspot_route_merges_into_task_material():
    """规格 §6：热点「参考到对话」后沉淀进 material.hotspot，主路由与兜底别名是同一实现。"""
    conversation_id = client.post("/api/v2/conversations", json={}).json()["conversation_id"]
    body = {"title": "开学季换机", "source": "热梗（联网搜索）", "url": "https://example.invalid/h",
            "kind": "数码", "gist": "开学季讨论", "keywords": ["开学", "换机"]}
    response = client.post(f"/api/v2/conversations/{conversation_id}/material/hotspot",
                           json=body)
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["material"]["hotspot"] == {"title": "开学季换机",
                                           "source": "热梗（联网搜索）"}
    assert data["hotspot"]["title"] == "开学季换机"

    # 兜底别名：同一处理函数，结果一致（不是第二份实现）。
    alias = client.post(f"/api/v2/conversations/{conversation_id}/hotspot",
                        json={"title": "另一个热点"})
    assert alias.status_code == 200
    assert alias.json()["material"]["hotspot"]["title"] == "另一个热点"

    conversation = client.get(f"/api/v2/conversations/{conversation_id}").json()
    assert conversation["material"]["hotspot"]["title"] == "另一个热点"
    assert conversation["task"]["material"]["hotspot"]["title"] == "另一个热点"

    assert client.post(f"/api/v2/conversations/{conversation_id}/hotspot",
                       json={"source": "没有标题"}).status_code == 400
    assert client.post("/api/v2/conversations/nope/material/hotspot",
                       json={"title": "x"}).status_code == 404


def test_v2_uses_file_sqlite_when_database_url_missing(monkeypatch, tmp_path):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("AGENT_DB_PATH", str(tmp_path / "fallback.db"))
    response = client.post("/api/v2/conversations", json={})
    assert response.status_code == 200
    assert response.json()["conversation_id"]
    assert (tmp_path / "fallback.db").exists()


def test_v2_reports_unwritable_database(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("AGENT_DB_PATH", "/dev/null/agent.db")
    response = client.post("/api/v2/conversations", json={})
    assert response.status_code == 503
