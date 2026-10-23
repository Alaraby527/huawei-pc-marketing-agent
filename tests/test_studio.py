"""图文工作室: the human-driven half must not be a weaker path than the agent's half.

Two things are load-bearing here:

* the studio inherits the conversation's fixed product and the verified KB original photos,
  so the user does not retype anything;
* an edited copy is saved through **the agent's own fact check**. A person typing in a
  browser must not be able to slip an unverified figure past the checker the agent has to
  satisfy, otherwise the studio is a bypass rather than a workspace.
"""
import asyncio
import os

import pytest
from sqlalchemy import select

from app import db as db_layer
from app import models, studio
from app.contracts import Fact, Source


def _run(coro):
    return asyncio.run(coro)


class _Product:
    model = "HUAWEI MateBook Studio Test"
    product_id = "P-studio"
    issues = []
    docs = []
    facts = [Fact(fact_id="F-MEM", product_id="P-studio", text="内存：16 GB", conditions="",
                  source=Source(kind="local_official_export", title="合成材料",
                                locator="规格", excerpt="16 GB"))]


class _Catalog:
    products = [_Product()]


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'studio.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    db_layer.reset_for_tests(url)
    db_layer.init_db()
    studio.reset_cache()
    # 「最近生成」是模块级内存，测试之间必须隔离，否则 c-studio 的桶会跨测试累积。
    studio.reset_generated()
    yield
    studio.reset_cache()
    studio.reset_generated()
    db_layer.reset_for_tests(url)


def _conversation(db, conversation_id="c-studio"):
    row = models.AgentConversation(id=conversation_id, workspace_id="default")
    db.add(row)
    db.flush()
    return row


def _pin_materials(monkeypatch, images=None):
    async def catalog():
        return _Catalog()

    async def fake_images(*args, **kwargs):
        return images if images is not None else [
            {"image_id": "I-1", "caption": "原野绿正面产品展示", "model_verified": True,
             "access_url": "/api/v1/assets/ref-token", "verification": "verified"}], None

    monkeypatch.setattr(studio, "_catalog", catalog)
    monkeypatch.setattr(studio, "verified_images", fake_images)


def _pin_product(session, conversation):
    """A conversation settles on a product; without one there is no reference photo."""
    session.add(models.AgentTaskState(conversation_id=conversation.id, stage="completed",
                                      data={"candidates": [{"product_id": "P-studio",
                                                            "model": _Product.model}]}))
    session.commit()


# ------------------------------------------------------------------ materials

def test_materials_are_honest_when_nothing_is_pinned_yet(monkeypatch):
    _pin_materials(monkeypatch)
    session = db_layer.session_factory()()
    conversation = _conversation(session)
    data = _run(studio.materials(session, conversation))
    assert data["product"]["product_id"] == ""
    assert data["images"] == [] and data["copy"] is None
    assert data["capability"]["modes"] == ["img2img", "kb_original"]
    assert "不是产品实物照" in data["notice"]
    session.close()


def test_materials_carry_the_fixed_product_and_the_current_copy(monkeypatch):
    _pin_materials(monkeypatch)
    session = db_layer.session_factory()()
    conversation = _conversation(session)
    session.add(models.AgentTaskState(conversation_id=conversation.id, stage="completed",
                                      data={"candidates": [{"product_id": "P-studio",
                                                            "model": _Product.model}],
                                            "artifact_count": 1}))
    artifact = models.AgentArtifact(conversation_id=conversation.id, artifact_type="post",
                                    latest_revision=1)
    session.add(artifact)
    session.flush()
    session.add(models.AgentArtifactRevision(
        artifact_id=artifact.id, revision=1, content={"title": "原稿", "body": "正文"},
        content_hash="abc", check_status={"passed": True}))
    session.commit()

    data = _run(studio.materials(session, conversation))
    assert data["product"]["product_id"] == "P-studio"
    assert data["product"]["model"] == _Product.model
    assert data["images"][0]["image_id"] == "I-1"
    assert data["copy"]["revision"] == 1 and data["copy"]["content"]["title"] == "原稿"
    assert data["copy"]["artifact_id"] == artifact.id
    session.close()


# ------------------------------------------------------------------ copy saving

def test_an_empty_copy_is_refused():
    session = db_layer.session_factory()()
    conversation = _conversation(session)
    result = _run(studio.save_copy_async(session, conversation, content={}))
    assert result["ok"] is False and result["error"] == "CONTENT_REQUIRED"
    session.close()


def test_an_edited_copy_runs_the_agents_fact_check(monkeypatch):
    """The studio must not be a way around verification."""
    _pin_materials(monkeypatch)
    session = db_layer.session_factory()()
    conversation = _conversation(session)
    session.add(models.AgentTaskState(conversation_id=conversation.id, stage="completed",
                                      data={"candidates": [{"product_id": "P-studio",
                                                            "model": _Product.model}]}))
    session.commit()

    # The facts say 16 GB, so a human typing 32 GB must be stopped.
    result = _run(studio.save_copy_async(
        session, conversation,
        content={"title": "内存够用吗", "body": "这台机器是 32 GB 内存。"},
        product_id="P-studio"))
    assert result["ok"] is False
    assert result["error"] == "FACT_CHECK_FAILED"
    session.close()


def test_a_correct_edited_copy_is_saved_as_a_new_revision(monkeypatch):
    _pin_materials(monkeypatch)
    session = db_layer.session_factory()()
    conversation = _conversation(session)
    session.add(models.AgentTaskState(conversation_id=conversation.id, stage="completed",
                                      data={"candidates": [{"product_id": "P-studio",
                                                            "model": _Product.model}]}))
    session.commit()

    result = _run(studio.save_copy_async(
        session, conversation, content={"title": "内存 16 GB 够用吗", "body": "内存 16 GB。"},
        product_id="P-studio"))
    assert result["ok"] is True, result
    assert result["revision"] == 1
    session.commit()
    revisions = session.scalars(select(models.AgentArtifactRevision)).all()
    assert len(revisions) == 1
    session.close()


def test_a_stale_revision_is_refused(monkeypatch):
    """Two tabs editing one copy must not silently overwrite each other."""
    _pin_materials(monkeypatch)
    session = db_layer.session_factory()()
    conversation = _conversation(session)
    session.add(models.AgentTaskState(conversation_id=conversation.id, stage="completed",
                                      data={"candidates": [{"product_id": "P-studio",
                                                            "model": _Product.model}]}))
    session.commit()
    first = _run(studio.save_copy_async(session, conversation,
                                        content={"title": "一", "body": "内存 16 GB。"},
                                        product_id="P-studio"))
    session.commit()
    stale = _run(studio.save_copy_async(
        session, conversation, content={"title": "二", "body": "内存 16 GB。"},
        artifact_id=first["artifact_id"], base_revision=0, product_id="P-studio"))
    assert stale["ok"] is False and stale["error"] == "STALE_REVISION"
    session.close()


# ------------------------------------------------------------------ generation

def test_generation_is_refused_without_a_verified_reference(monkeypatch):
    _pin_materials(monkeypatch, images=[])
    session = db_layer.session_factory()()
    conversation = _conversation(session)
    result = _run(studio.generate_image(session, conversation, prompt="x"))
    assert result["ok"] is False and result["error"] == "NO_REFERENCE_IMAGE"
    assert "在对话里确认产品" in result["message"]
    session.close()


def test_generation_is_refused_when_the_model_lacks_reference_support(monkeypatch):
    _pin_materials(monkeypatch)
    monkeypatch.setenv("IMAGE_REFERENCE_MODEL", "gpt-image-2-w")
    session = db_layer.session_factory()()
    conversation = _conversation(session)
    _pin_product(session, conversation)
    result = _run(studio.generate_image(session, conversation, prompt="x"))
    assert result["ok"] is False and result["error"] == "CAPABILITY_UNAVAILABLE"
    assert result["capability"]["supports"] is False
    session.close()


def test_an_expired_reference_is_a_clear_refusal(monkeypatch):
    _pin_materials(monkeypatch)
    monkeypatch.setattr(studio, "get_asset", lambda token: None)
    session = db_layer.session_factory()()
    conversation = _conversation(session)
    _pin_product(session, conversation)
    result = _run(studio.generate_image(session, conversation, prompt="x"))
    assert result["ok"] is False and result["error"] == "REFERENCE_EXPIRED"
    session.close()


def test_generated_images_are_labelled_and_never_pass_as_product_photos(monkeypatch):
    _pin_materials(monkeypatch)
    monkeypatch.setattr(studio, "get_asset", lambda token: (b"\x89PNG-data", "image/png"))
    monkeypatch.setattr(studio, "store_asset", lambda body, mime: "/api/v1/assets/out-1")

    async def fake_thread(fn, *args):
        return [b"generated-png"], "primary"

    monkeypatch.setattr(studio.asyncio, "to_thread", fake_thread)
    session = db_layer.session_factory()()
    conversation = _conversation(session)
    _pin_product(session, conversation)
    result = _run(studio.generate_image(session, conversation, prompt="宿舍书桌", ratio="3:4"))
    assert result["ok"] is True
    assert result["images"][0]["kind"] == "generated_scene"
    assert result["reference"]["image_id"] == "I-1"
    assert "不是产品实物照" in result["notice"]
    assert result["capability"]["state"] == "verified_yes"
    session.close()

def test_an_edit_without_an_explicit_id_continues_the_existing_artifact(monkeypatch):
    """Saving must version the copy that exists, not fork a second artifact at revision 1."""
    _pin_materials(monkeypatch)
    session = db_layer.session_factory()()
    conversation = _conversation(session)
    _pin_product(session, conversation)
    first = _run(studio.save_copy_async(session, conversation,
                                        content={"title": "一", "body": "内存 16 GB。"},
                                        product_id="P-studio"))
    session.commit()
    second = _run(studio.save_copy_async(session, conversation,
                                         content={"title": "二", "body": "内存 16 GB。"},
                                         product_id="P-studio"))
    session.commit()
    assert second["artifact_id"] == first["artifact_id"], "同一次创作只应有一个交付物"
    assert second["revision"] == 2
    artifacts = session.scalars(select(models.AgentArtifact)).all()
    assert len(artifacts) == 1
    session.close()


def test_provenance_survives_into_the_studio_materials(monkeypatch):
    """Dropping `source` degraded every rights status to "unknown".

    The KB records where an image came from; the studio must not throw that away, or the
    rights report becomes less truthful than the data it came from.
    """
    async def catalog():
        return _Catalog()

    async def fake_images(*args, **kwargs):
        return [{"image_id": "I-1", "caption": "原野绿多彩配色", "model_verified": True,
                 "access_url": "/api/v1/assets/tok",
                 "source": {"kind": "local_official_export", "title": "原野绿多彩配色_04.png"},
                 "verification": {"method": "local_official_export_index", "sha256": "b" * 64}}], None

    monkeypatch.setattr(studio, "_catalog", catalog)
    monkeypatch.setattr(studio, "verified_images", fake_images)
    session = db_layer.session_factory()()
    conversation = _conversation(session)
    session.add(models.AgentTaskState(conversation_id=conversation.id, stage="completed",
                                      data={"candidates": [{"product_id": "P-studio",
                                                            "model": _Product.model}]}))
    session.commit()
    data = _run(studio.materials(session, conversation))
    from app import assets
    assert data["images"][0]["source"]["kind"] == "local_official_export"
    assert data["assets"]["by_rights"] == {assets.RIGHTS_PROVENANCE_ONLY: 1}
    assert data["assets"]["assets"][0]["sha256"] == "b" * 64
    session.close()


def test_the_workbench_endpoints_exist_and_are_wired():
    """图文工作室三栏要真的能用：配方列表 / 文案优化 / 最近生成。"""
    from app.main import app
    paths = {getattr(r, "path", "") for r in app.routes}
    assert "/api/v1/recipes" in paths
    assert any("/studio/copy/optimize" in p for p in paths)
    assert any("/studio/generated" in p for p in paths)


def test_copy_optimisation_only_changes_expression():
    """优化最容易被做成"加料"，所以提示词必须把事实边界写死。

    用户实测反馈"优化后好像没有效果"，所以提示词还必须把**改动幅度**写成硬性要求
    （重写开头钩子 / 重排段落节奏 / 平台语感），并且要带回真实改动比例。
    """
    import inspect
    from app import studio
    source = inspect.getsource(studio.optimize_copy)
    # 事实边界（原有结论，一条都不能少）
    assert "只改表达，不新增事实" in source
    assert "不得增加卖点、参数、价格、优惠、承诺" in source
    assert "不得替换或改写型号" in source
    assert '"changes"' in source and '"kept"' in source, "要带回改了什么、保留了什么"
    # 改动幅度（这轮新增：不能只抠字眼）
    assert "重写开头钩子" in source
    assert "较大改动" in source, "changes 要描述真实做过的较大改动"
    assert "至少 3 条" in source, "changes 不能只给一两条就交差"
    assert '"约/最高/选配/仅某版本"等限定词必须原样保留' in source
    assert "不得新增实测经历、口碑、排名" in source
    assert "changed_ratio" in source, "要给出真实改动比例，幅度小就要能看出来"
    assert "changes_source" in source, "模型没回报 changes 时要用真实 diff 兜底，并标明来源"


def test_optimise_retries_once_when_the_model_returns_an_empty_rewrite(monkeypatch):
    """实测：同一段真实小红书文案偶发只回 changes/kept、optimized 为空（模型抖动）。

    要重试一次；仍拿不到就如实报 EMPTY_RESULT，不拿原文冒充"已经优化过"。
    """
    _pin_materials(monkeypatch)
    calls = {"n": 0}

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"changes": ["重写开头"], "kept": ["参数"], "optimized": ""}, {"usage": {}}
        return {"changes": [], "kept": [], "optimized": "换了个说法的整篇文案。"}, {"usage": {}}

    monkeypatch.setattr("app.model.chat_json_messages", flaky)
    session = db_layer.session_factory()()
    conversation = _conversation(session)
    _pin_product(session, conversation)
    result = _run(studio.optimize_copy(session, conversation, content="原来的说法。"))
    assert calls["n"] == 2, "空结果要重试一次"
    assert result["ok"] is True and result["optimized"] == "换了个说法的整篇文案。"
    # 第一次拿到的 changes/kept 不能丢
    assert result["changes"] == ["重写开头"] and result["kept"] == ["参数"]

    # 两次都空：必须如实报错
    monkeypatch.setattr("app.model.chat_json_messages", lambda *a, **k: ({"optimized": ""}, {}))
    failed = _run(studio.optimize_copy(session, conversation, content="原来的说法。"))
    assert failed["ok"] is False and failed["error"] == "EMPTY_RESULT"
    session.close()


def test_optimise_never_returns_an_empty_change_list(monkeypatch):
    """实测（真实整篇小红书文案）：模型有时只回 optimized，changes/kept 是空的。

    空 changes 会让用户完全"感知不到效果"，所以要用真实 diff 兜底，
    并如实标明这是 diff 得出的、不是模型的自述。
    """
    _pin_materials(monkeypatch)
    monkeypatch.setattr("app.model.chat_json_messages", lambda *a, **k: (
        {"optimized": "全新开头！\n\n第二段换了说法。\n\n参数 16 GB 原样保留。"}, {"usage": {}}))
    session = db_layer.session_factory()()
    conversation = _conversation(session)
    _pin_product(session, conversation)
    result = _run(studio.optimize_copy(
        session, conversation,
        content="屏幕是触控的。\n\n内存 16 GB。\n\n参数 16 GB 原样保留。"))
    assert result["ok"] is True, result
    assert result["changes"], "改动清单不能是空白的"
    assert result["changes_source"] == "derived_from_diff"
    assert result["changed_ratio"] > 0
    session.close()


def test_optimise_reports_the_real_change_ratio_and_keeps_facts(monkeypatch):
    """只有一行 note 的"已优化"说服不了用户：要给出真实字符改动比例。"""
    _pin_materials(monkeypatch)

    # chat_json_messages 是同步函数（内部自己处理超时），这里不能用 async 替身。
    monkeypatch.setattr("app.model.chat_json_messages", lambda *a, **k: (
        {"optimized": "开机就惊艳！\n\n屏幕手感顺滑。",
         "changes": ["重写开头钩子", "拆成短句"],
         "kept": ["型号与参数原样保留"]}, {"usage": {}}))
    session = db_layer.session_factory()()
    conversation = _conversation(session)
    _pin_product(session, conversation)
    result = _run(studio.optimize_copy(session, conversation, content="屏幕是触控的。"))
    assert result["ok"] is True, result
    assert result["changes"] and result["kept"]
    assert result["changes_source"] == "model"
    assert 0.0 <= result["changed_ratio"] <= 1.0
    assert result["changed_ratio"] > 0, "整篇重写过，改动比例不可能是 0"
    session.close()


def test_every_generated_image_records_what_a_redraw_needs(monkeypatch):
    """重绘 = 把当初那条流程再走一遍，所以每张图的来源必须逐张记住：
    参考图（image_id / 地址）、用户原始提示词、配方、模型、比例 / 质量 / 张数。"""
    _pin_materials(monkeypatch)
    monkeypatch.setattr(studio, "get_asset", lambda token: (b"\x89PNG-data", "image/png"))
    monkeypatch.setattr(studio, "store_asset", lambda body, mime: "/api/v1/assets/out-1")

    async def fake_thread(fn, *args):
        return [b"generated-png-1", b"generated-png-2"], "primary"

    monkeypatch.setattr(studio.asyncio, "to_thread", fake_thread)
    session = db_layer.session_factory()()
    conversation = _conversation(session)
    _pin_product(session, conversation)
    result = _run(studio.generate_image(
        session, conversation, prompt="宿舍书桌", ratio="portrait", image_id="I-1",
        size="1080x1440", count=2))
    assert result["ok"] is True, result
    rows = studio.generated_history(conversation.id)
    assert len(rows) == 2
    for row in rows:
        assert row["image_id"] == "I-1"                       # 原来的参考图
        assert row["reference_url"] == "/api/v1/assets/ref-token"
        assert row["reference_kind"] == "kb_original"
        assert row["source_prompt"] == "宿舍书桌"              # 用户原始提示词，重绘要用它
        assert row["composed_prompt"]
        assert row["model"] and row["ratio"] == "portrait"
        assert row["size"] == "1080x1440" and row["count"] == 2
        assert row["quality"] == "high"
        assert row["generated_id"]
    assert len({row["generated_id"] for row in rows}) == 2, "每张图要有独立标识"
    assert result["images"][0]["generated_id"] == rows[0]["generated_id"]
    studio.reset_generated()
    session.close()


def test_a_generated_image_can_be_used_as_the_next_reference(monkeypatch):
    """右栏「新生成产品图」单击设为参考图后，生成接口必须真的用它。

    拿一个知识库里不存在的 id 去 ``pick_reference`` 会**静默回退到第一张原图**，
    那就成了"看着用了生成图、实际用了别的原图"，所以生成图用 ``gen:<generated_id>`` 标明。
    """
    _pin_materials(monkeypatch)
    monkeypatch.setattr(studio, "get_asset", lambda token: (b"\x89PNG-data", "image/png"))
    monkeypatch.setattr(studio, "store_asset", lambda body, mime: "/api/v1/assets/out-1")

    async def fake_thread(fn, *args):
        return [b"generated-png"], "primary"

    monkeypatch.setattr(studio.asyncio, "to_thread", fake_thread)
    session = db_layer.session_factory()()
    conversation = _conversation(session)
    _pin_product(session, conversation)
    first = _run(studio.generate_image(session, conversation, prompt="第一次"))
    gen_id = first["images"][0]["generated_id"]
    second = _run(studio.generate_image(session, conversation, prompt="接着改",
                                        image_id="gen:" + gen_id))
    assert second["ok"] is True, second
    assert second["reference"]["kind"] == "generated"
    assert second["reference"]["image_id"] == "gen:" + gen_id
    rows = studio.generated_history(conversation.id)
    assert rows[0]["reference_kind"] == "generated"
    assert rows[0]["reference_url"] == "/api/v1/assets/out-1"
    studio.reset_generated()
    session.close()


def test_an_unknown_generated_reference_is_refused_not_silently_replaced(monkeypatch):
    """最危险的是"看着用了生成图、实际用了别的原图"：必须显式拒绝。"""
    _pin_materials(monkeypatch)
    session = db_layer.session_factory()()
    conversation = _conversation(session)
    _pin_product(session, conversation)
    result = _run(studio.generate_image(session, conversation, prompt="x",
                                        image_id="gen:nope"))
    assert result["ok"] is False and result["error"] == "REFERENCE_NOT_FOUND"
    session.close()


def test_the_generated_grid_and_the_zoom_are_wired_and_kb_has_no_redraw():
    """需求1/2/3/4 的区分必须钉在代码里：新生成产品图区块 + 应用内放大 + 两种图动作不同。"""
    from pathlib import Path
    html = Path("app/static/index.html").read_text()
    js = Path("app/static/app.js").read_text()
    for element in ("studio-generated-grid", "studio-image-zoom", "studio-image-zoom-close",
                    "studio-image-zoom-download", "studio-image-zoom-ref",
                    "studio-image-zoom-redraw"):
        # 放大浮层是动态创建的（overlay.id / #id 选择器），所以检查标识确实在代码里被使用。
        assert element in js, element
    # 区块在右栏、知识库原图下方
    assert html.index('id="studio-kb-grid"') < html.index('id="studio-generated-grid"')
    assert "on('studio-generated-grid', 'click'" in js
    assert "on('studio-generated-grid', 'dblclick'" in js
    # loading 占位是**一张真的卡片 + 转圈**，不是只改按钮文字
    assert '"studio-loading"' in js and '"studio-spinner"' in js
    assert "studioState.generatedLoading" in js
    # 双击＝应用内浮层，不许 window.open
    zoom_at = js.index("  function openStudioImageZoom(")
    zoom_body = js[zoom_at:js.index("  function closeStudioImageZoom(", zoom_at)]
    assert "window.open" not in zoom_body
    # 区分：知识库原图双击后不出现「重绘」按钮
    assert "redraw.hidden = payload.kind !== 'generated'" in js
    # 旧后端不支持 gen: 参考图时必须拦住（否则会静默回退到第一张原图）
    assert "studioState.capability.generated_reference" in js
    assert '"generated_reference": True' in Path("app/studio.py").read_text()
    # 重绘要复用当初记录的参考图 / 提示词 / 参数，而不是当前表单
    assert "source_prompt" in js and "reference_url" in js


def test_generated_history_is_honest_about_persistence():
    """内存保存就说内存保存，不假装是持久化历史。"""
    import inspect
    from app import studio
    assert "会话级、内存保存" in inspect.getsource(studio)
    assert studio.generated_history("nobody") == []
    studio.reset_generated()


def test_the_studio_view_has_three_columns_and_no_placeholder():
    from pathlib import Path
    html = Path("app/static/index.html").read_text()
    assert "studio-col-copy" in html and "studio-col-image" in html and "studio-col-kb" in html
    assert "studio-history" in html
    # 用户要求删掉的旧内容不得残留
    assert "待设计" not in html
    assert "打开现有工作台" not in html and "打开快图设计" not in html
    # 左栏三个按钮
    for button in ("studio-regen", "studio-optimize", "studio-copy"):
        assert button in html, button


def test_the_endpoint_passes_recipe_and_count_through():
    """踩过：前端发了 recipe_id，接口却忽略了，等于配方功能没接上。"""
    import inspect
    from app import main
    source = inspect.getsource(main.v2_studio_generate)
    assert 'recipe_id=str(payload.get("recipe_id") or "")' in source
    assert 'count=int(payload.get("count") or 1)' in source


def test_count_is_capped_and_reported():
    """张数之前写死为 1，"张数"是假的。现在要真的传下去，且如实回报。"""
    import inspect
    from app import studio
    source = inspect.getsource(studio.generate_image)
    assert "wanted = max(1, min(int(count or 1), 4))" in source
    assert '"requested_count": wanted' in source
    assert '"size": size or "auto"' in source
    assert "generate_from_product, body, text, mime or \"image/png\", resolved,\n            size or None, wanted" in source


def test_image_models_endpoint_reports_probe_evidence_not_guesses():
    """工作室始终带参考图，所以"能否吃参考图"是首要信息；不能吃的必须标出来。"""
    from app.main import image_models_endpoint
    data = image_models_endpoint()
    assert data["ok"] is True
    by = {row["model"]: row for row in data["models"]}
    assert by["gpt-image-2.5"]["supports_reference"] is True
    assert by["gpt-image-2-w"]["supports_reference"] is False
    assert "不支持参考图" in by["gpt-image-2-w"]["status"]
    assert by["gpt-image-2.5"]["evidence"], "必须带探针证据"
    assert "不编造" in data["note"], "没有价格数据就要说明，不能编"
    # 可用的排在前面，避免用户先看到必然失败的模型
    assert data["models"][0]["supports_reference"] is True


def test_the_frontend_actually_sends_count_and_size_and_fills_models():
    from pathlib import Path
    js = Path("app/static/app.js").read_text()
    assert "renderStudioModels" in js and "studioState.models" in js
    assert "count: count," in js and "size: size," in js and "recipe_id:" in js
    assert "'/api/v1/image-models'" in js
    html = Path("app/static/index.html").read_text()
    assert 'id="studio-count"' in html and 'value="4"' in html


def test_catalog_loading_is_not_gated_behind_a_session():
    """实测 bug：配方/模型下拉是空的，看起来像"点不开的假按钮"。

    根因是它们和 materials 挤在同一个 Promise.all 里、且被 `if (!sessionId) return` 挡在前面。
    但配方列表和模型列表**与会话无关**——所以必须无条件先加载。
    """
    from pathlib import Path
    js = Path("app/static/app.js").read_text()

    assert "async function loadStudioCatalog()" in js
    # 目录加载必须在 session 判断之前
    catalog_at = js.index("  async function loadStudio(force) {")
    body = js[catalog_at:catalog_at + 900]
    assert body.index("await loadStudioCatalog()") < body.index("if (!sessionId)"), \
        "必须先加载目录，再判断会话"
    # 页面初始化时也要加载（这样即便没进过板块，下拉也已就绪）
    assert js.count("loadStudioCatalog()") >= 3, "定义 + 初始化 + 进板块，至少 3 处"
    # 失败要有可见原因，不能停在"加载中…"
    assert "配方加载失败" in js and "模型列表为空" in js


def test_every_studio_control_is_bound():
    """用户反馈"全是假的按钮"，所以逐个钉住：每个控件都必须有事件绑定。"""
    from pathlib import Path
    js = Path("app/static/app.js").read_text()
    html = Path("app/static/index.html").read_text()
    for control in ("studio-kb-grid", "studio-prompt", "studio-generate", "studio-optimize",
                    "studio-history-refresh", "studio-copy", "studio-regen",
                    "studio-history-grid"):
        assert f'id="{control}"' in html, control
        assert f"on('{control}'" in js, f"{control} 没有绑定事件"
    assert "\n  bindStudio();" in js, "bindStudio 必须被调用"


def test_recipe_select_always_has_at_least_one_option():
    """空下拉会被当成坏控件，所以永远保留"通用风格"这一项。"""
    from pathlib import Path
    js = Path("app/static/app.js").read_text()
    assert "'<option value=\"\">通用风格（不用配方）</option>'" in js
    assert "（模型列表为空）" in js, "模型为空时也要给出可见结果"


def test_layout_document_is_complete_and_documented():
    """版式产出的是排版文档而不是图片，结构要能直接交给编辑器。"""
    from app import design_library
    built = design_library.build_document("V04-C1", {}, "HUAWEI MateBook 14")
    native = built["native_payload"]
    assert native["objects"], "必须有元素，否则文档是空的"
    assert built["engine"] == "vue-fabric-editor"
    manifest = built["manifest"]
    for key in ("canvas", "fonts", "lockedLayers", "assets", "source"):
        assert key in manifest, key
    sidecar = built["sidecar"]
    assert sidecar["layout_id"] == "V04-C1"
    checks = design_library.check_document(built)
    assert checks.get("passed") is True or checks.get("ok") is not False




def test_the_candidate_board_is_not_squeezed_into_a_tiny_scroller():
    """用户把候选卡看成"老 agent 的残留选项卡"，因为它被 46vh 的内部滚动框切成半截，
    而 macOS 默认隐藏滚动条，所以看起来就是坏掉的 UI。

    候选卡是核心功能（选型号），不是残留，不能删——要给它足够高度。
    """
    from pathlib import Path
    html = Path("app/static/index.html").read_text()
    css = Path("app/static/style.css").read_text()
    assert 'id="candidate-board"' in html, "候选卡是功能，必须保留"
    # 生效中的那条规则
    assert ".result-area{flex:0 1 auto;min-height:0;max-height:70vh" in css
    assert "max-height:46vh;overflow-y:auto" not in css, "不许再压回 46vh 小滚动框"
    # 结果区总是含示例栏与输入框，:empty 永不成立，所以不能用 flex-grow 抢高度
    assert ".result-area{flex:1 1 auto" not in css


# ------------------------------------- 并发 / 超时 / 提示词选填（2026-10-23）

def test_a_missing_prompt_is_composed_instead_of_refused(monkeypatch):
    """提示词改成选填：留空时后端必须自己拼出提示词，不能空着发给上游、也不能拒绝。

    路径（app/studio.py 的 generate_image）：显式 recipe_id → 该配方；
    否则按本次会话锁定的交付面自动挑一个**已批准**配方；都没有才退回通用提示词。
    """
    _pin_materials(monkeypatch)
    monkeypatch.setattr(studio, "get_asset", lambda token: (b"\x89PNG-data", "image/png"))
    monkeypatch.setattr(studio, "store_asset", lambda body, mime: "/api/v1/assets/out-1")
    seen: dict = {}

    async def fake_thread(fn, *args):
        # 调用形态：generate_from_product(body, text, mime, model, size, n, ratio, timeout)
        seen["composed"] = args[1]
        return [b"generated-png"], "primary"

    monkeypatch.setattr(studio.asyncio, "to_thread", fake_thread)
    session = db_layer.session_factory()()
    conversation = _conversation(session)
    _pin_product(session, conversation)
    result = _run(studio.generate_image(session, conversation, prompt=""))
    assert result["ok"] is True, result
    assert result["prompt"].strip(), "留空也要拼出提示词"
    assert seen["composed"] == result["prompt"]
    # 如实说明这次为什么没用配方（这个会话没有锁定交付面），不算假装用了配方。
    assert result["recipe"]["recipe_id"] is None
    assert result["recipe"]["why_no_recipe"]
    session.close()


def test_the_prompt_field_is_optional_in_the_ui_and_the_js():
    """界面要明确写"选填"，前端也不能再拦「请先写提示词」。"""
    from pathlib import Path
    html = Path("app/static/index.html").read_text()
    js = Path("app/static/app.js").read_text()
    assert 'id="studio-prompt"' in html, "id 不许删"
    # 只看"提示词"这一行的字段头，别把上面参考图的"必填"一起框进来
    head_at = html.index("<span>提示词</span>")
    prompt_head = html[head_at:head_at + 120]
    assert "选填" in prompt_head, "提示词必须明确标成选填"
    assert "必填" not in prompt_head, "提示词不得再标必填"
    assert "请先写提示词" not in js, "前端不能再因为提示词为空把人挡回去"


def test_the_image_timeout_defaults_to_two_minutes_and_still_reads_the_env(monkeypatch):
    """上游卡死时用户不该干等 5 分钟：默认 120 秒，但仍走 IMAGE_TIMEOUT_SECONDS 覆盖。"""
    _pin_materials(monkeypatch)
    monkeypatch.setattr(studio, "get_asset", lambda token: (b"\x89PNG-data", "image/png"))
    monkeypatch.setattr(studio, "store_asset", lambda body, mime: "/api/v1/assets/out-1")
    seen: dict = {}

    async def fake_thread(fn, *args):
        seen["timeout"] = args[-1]
        return [b"generated-png"], "primary"

    monkeypatch.setattr(studio.asyncio, "to_thread", fake_thread)
    session = db_layer.session_factory()()
    conversation = _conversation(session)
    _pin_product(session, conversation)

    monkeypatch.delenv("IMAGE_TIMEOUT_SECONDS", raising=False)
    _run(studio.generate_image(session, conversation, prompt="x"))
    assert seen["timeout"] == 120

    monkeypatch.setenv("IMAGE_TIMEOUT_SECONDS", "240")
    _run(studio.generate_image(session, conversation, prompt="x"))
    assert seen["timeout"] == 240, "环境变量仍然能覆盖默认值"
    studio.reset_generated()
    session.close()


def test_the_frontend_fans_out_concurrent_single_image_requests():
    """实测"串行"的根因不是前端循环，也不是后端锁：前端只发**一个** count=N 的请求，
    后端把它转成上游一次 n=N 的调用整体 await，N 张因此绑在同一个响应里。

    现在前端要并发发起 N 个 count=1 的请求，并发上限 4，并且每张完成就替换自己的占位、
    不等全部完成。纯前端改动没有 JS 测试框架，所以按本文件既有做法钉住契约字符串。
    """
    from pathlib import Path
    js = Path("app/static/app.js").read_text()
    assert "const STUDIO_MAX_CONCURRENCY = 4" in js, "并发上限必须是 4"
    assert "Object.assign({}, payload, {count: 1})" in js, "每张一个独立的 count=1 请求"
    assert "Promise.all(jobs)" in js, "多个请求要并发发起，不是顺序 await"
    assert "studioState.generatedLoading = wanted" in js, "先画 N 张占位"
    assert "studioState.generatedLoading = Math.max(0, Number(studioState.generatedLoading || 0) - 1)" in js
    # 部分失败要如实报，不合并成一句成功
    assert "部分成功：" in js
    # 后端单次 count=N 的路径保持兼容，没有被删掉
    from app import studio as studio_mod
    import inspect
    source = inspect.getsource(studio_mod.generate_image)
    assert "wanted = max(1, min(int(count or 1), 4))" in source
    assert '"requested_count": wanted' in source


def test_concurrent_generations_do_not_collide_on_the_ledger(monkeypatch):
    """并发实测（2026-10-23 上游恢复后，4 张 count=1 打到运行中的服务）：

    3 张返回 HTTP 500，数据库里只多了一条计费记录。根因是计费记录只 flush 不 commit，
    写事务横跨整个 30–100 秒的阻塞生图调用；SQLite 是单写者，后来者写自己的计费记录
    等到 busy_timeout 超时 → "database is locked"。

    所以 generate_image 必须在进上游之前提交这次落账。这里用 4 个并发调用 + 独立会话复现：
    修好之后 4 张必须全部成功。
    """
    import time as time_mod

    _pin_materials(monkeypatch)
    monkeypatch.setattr(studio, "get_asset", lambda token: (b"\x89PNG-data", "image/png"))
    monkeypatch.setattr(studio, "store_asset", lambda body, mime: "/api/v1/assets/out-1")

    async def fake_thread(fn, *args):
        # 模拟"阻塞"的上游调用：事务如果还开着，后面的并发调用就会撞写锁。
        await asyncio.sleep(0.2)
        return [b"generated-png"], "primary"

    monkeypatch.setattr(studio.asyncio, "to_thread", fake_thread)
    setup = db_layer.session_factory()()
    conversation = _conversation(setup, "c-concurrent")
    _pin_product(setup, conversation)
    setup.commit()
    setup.close()

    async def one(index):
        own = db_layer.session_factory()()
        try:
            row = own.get(models.AgentConversation, "c-concurrent")
            return await studio.generate_image(own, row, prompt="并发第 " + str(index) + " 张")
        finally:
            own.close()

    async def four():
        return await asyncio.gather(*[one(i) for i in range(4)])

    started = time_mod.monotonic()
    results = _run(four())
    elapsed = time_mod.monotonic() - started
    assert [row.get("ok") for row in results] == [True, True, True, True], results
    assert elapsed < 0.7, "4 个 0.2 秒的调用必须重叠，不能串成 0.8 秒以上"
    assert len(studio.generated_history("c-concurrent")) == 4
    studio.reset_generated()
