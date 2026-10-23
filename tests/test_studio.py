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
    yield
    studio.reset_cache()
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
    """优化最容易被做成"加料"，所以提示词必须把事实边界写死。"""
    import inspect
    from app import studio
    source = inspect.getsource(studio.optimize_copy)
    assert "只改表达，不新增事实" in source
    assert "不得增加卖点、参数、价格、优惠、承诺" in source
    assert "不得替换或改写型号" in source
    assert '"changes"' in source and '"kept"' in source, "要带回改了什么、保留了什么"


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
