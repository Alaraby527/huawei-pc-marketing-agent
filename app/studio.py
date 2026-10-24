"""图文工作室 (graphics & copy studio): the user-driven half of the pipeline.

The flow the user described:

    conversation -> copy -> the knowledge-base original photo is brought out
        -> the user chooses: go to the studio, or keep editing the copy
        -> in the studio the user generates images and edits the copy

So this module carries the conversation's context into the studio - the fixed product and
the rough direction of this creation - and exposes three things the studio page needs:

* ``materials``      the product, the current copy, and the verified KB original photos
* ``save_copy``      save an edited copy **through the same fact check as the agent**
* ``generate_image`` reference photo + prompt -> scene image, capability-gated

The copy path deliberately reuses the agent's own save routine rather than writing a row
directly: a human editing text in a browser must not be able to slip an unverified figure
past the checker that the agent itself has to satisfy.
"""
from __future__ import annotations

import asyncio
import difflib
import hashlib
import os
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import agent_runtime, assets, image_capabilities, models
from .contracts import Platform
from .evidence import load_catalog, verified_images
from .retrieval import get_asset, store_asset

_CATALOG: dict[str, tuple[float, object]] = {}
_CATALOG_TTL = 120.0

# 与前端参考图上限一致（app/static/app.js 的 applyPendingRefs 最多勾 9 张）：多款型号一起
# 导入时在后端就截到这个上限，避免返回前端明知道会被丢掉的那些图。
_STUDIO_IMAGE_LIMIT = 9


async def _catalog():
    """Load the knowledge base with a short cache: the studio page asks repeatedly."""
    cached = _CATALOG.get("default")
    if cached and time.monotonic() - cached[0] < _CATALOG_TTL:
        return cached[1]
    catalog = await load_catalog("", "")
    _CATALOG["default"] = (time.monotonic(), catalog)
    return catalog


def reset_cache() -> None:
    _CATALOG.clear()


class _StudioContext:
    """Just enough of a ToolContext for the agent's own artifact saver.

    Reusing ``_save_artifact`` means the studio inherits its revision handling, its
    fact-check gate and its STALE_REVISION guard instead of re-implementing a weaker copy.
    The run is a stub because saving copy outside a run has no run to attach to.
    """

    def __init__(self, db: Session, conversation: models.AgentConversation, catalog,
                 last_search: dict | None = None):
        self.db = db
        self.conversation = conversation
        self.catalog = catalog
        self.last_search = last_search or {}
        self.read_product_ids: list[str] = []
        self.run = None
        self.events: list = []


def _fixed_product(task: dict) -> dict:
    """The product settled earlier in the conversation, if there is one.

    Reads the new archive first (``material.models`` / ``selected_models``, 1–2 models).
    A single legacy candidate is also readable (``task_material.normalize`` admits only that
    unambiguous case). Everything else has no recorded choice, and returns nothing: picking
    ``candidates[0]`` out of several search results attributed the task to a model the user
    never selected.
    """
    rows = _fixed_products(task)
    return rows[0] if rows else {}


def _fixed_products(task: dict) -> list[dict]:
    """Every settled model, in the user's selection order."""
    from . import task_material

    models = task_material.normalize(task).get("models") or []
    candidates = [row for row in (task.get("candidates") or []) if isinstance(row, dict)]
    by_id = {str(row.get("product_id")): row for row in candidates}
    rows: list[dict] = []
    for row in models:
        product_id = str(row.get("product_id") or "")
        if not product_id:
            continue
        legacy = by_id.get(product_id) or {}
        rows.append({"product_id": product_id,
                     "model": row.get("model") or legacy.get("model") or "",
                     "facts_count": legacy.get("facts_count")})
    return rows


def _latest_artifact(db: Session, conversation_id: str):
    artifact = db.scalars(select(models.AgentArtifact).where(
        models.AgentArtifact.conversation_id == conversation_id).order_by(
        models.AgentArtifact.created_at.desc())).first()
    if artifact is None:
        return None, None
    revision = db.scalars(select(models.AgentArtifactRevision).where(
        models.AgentArtifactRevision.artifact_id == artifact.id).order_by(
        models.AgentArtifactRevision.revision.desc())).first()
    return artifact, revision


def _raw_field(row, key: str):
    """Read a field without stringifying it.

    ``agent_runtime._image_field`` returns ``str(value)``, which is right for ids and captions
    but destroyed the ``source`` mapping here: the resolver received the repr of a dict as a
    string and could no longer find ``kind``, so every rights status degraded to "unknown".
    """
    if isinstance(row, dict):
        return row.get(key)
    return getattr(row, key, None)


async def materials(db: Session, conversation: models.AgentConversation) -> dict:
    """Everything the studio needs to open on this conversation."""
    state = db.get(models.AgentTaskState, conversation.id)
    task = dict(state.data or {}) if state else {}
    product_ref = _fixed_product(task)
    catalog = await _catalog()
    product = None
    if product_ref:
        product = agent_runtime._product(catalog, product_id=product_ref["product_id"])
    # Every settled model contributes its verified originals, not just the first: with 1–2
    # selected models the studio used to import only `_fixed_product(task)` (the first one),
    # so choosing MateBook 14（4 张）+ 酷睿 Ultra（6 张）imported 4 photos and silently
    # dropped the second model's 6. Deduped by image id and capped like the frontend's
    # reference picker (9), so nothing is lost or duplicated.
    images: list[dict] = []
    seen_images: set[str] = set()
    fixed_models = _fixed_products(task)
    for row_ref in fixed_models:
        row_product = agent_runtime._product(catalog, product_id=row_ref["product_id"])
        if row_product is None:
            continue
        try:
            rows, _ = await verified_images(catalog, row_product, Platform.XIAOHONGSHU,
                                            ["屏幕", "接口", "尺寸与重量", "电池"])
        except Exception:  # noqa: BLE001 - no image is a normal state, reported as empty
            continue
        for row in (rows or []):
            # `source` is carried through deliberately: it holds the provenance that the
            # asset resolver reads. Dropping it degraded every rights status to "unknown",
            # losing the more truthful "provenance recorded, terms unreviewed".
            image = {"image_id": agent_runtime._image_field(row, "image_id"),
                     # Owning model, so a 2-model task does not label the second model's
                     # photos with the first model's id in `assets`.
                     "product_id": row_ref["product_id"],
                     "caption": agent_runtime._image_field(row, "caption"),
                     "access_url": agent_runtime._image_field(row, "access_url"),
                     "model_verified": agent_runtime._image_field(row, "model_verified"),
                     "verification": _raw_field(row, "verification"),
                     "source": _raw_field(row, "source")}
            key = str(image.get("image_id") or image.get("access_url") or "")
            if key and key in seen_images:
                continue
            if key:
                seen_images.add(key)
            images.append(image)
            if len(images) >= _STUDIO_IMAGE_LIMIT:
                break
        if len(images) >= _STUDIO_IMAGE_LIMIT:
            break
    artifact, revision = _latest_artifact(db, conversation.id)
    copy = None
    if artifact is not None and revision is not None:
        copy = {"artifact_id": artifact.id, "artifact_type": artifact.artifact_type,
                "revision": revision.revision, "content": revision.content,
                "check": revision.check_status, "content_hash": revision.content_hash,
                "note": revision.note}
    # AgentConversation has no title column: the list view derives one from the task state
    # or the first message, and the studio must use the same source rather than invent one.
    summary = agent_runtime.conversation_summary(db, conversation)
    from . import task_material
    archive = task_material.normalize(task)
    return {
        "conversation_id": conversation.id,
        "title": summary.get("title", ""),
        # Always the same keys, even with nothing pinned: a client should not have to guess
        # whether a missing key means "no product" or "the field was dropped".
        "product": {"product_id": product_ref.get("product_id", ""),
                    "model": (product.model if product is not None
                              else product_ref.get("model", "")),
                    "facts_count": product_ref.get("facts_count"),
                    "audience": str(archive.get("audience") or ""),
                    "scenario": str(archive.get("scenario") or "")},
        # Multi-model selection (1–2) plus the accumulative archive, so the studio no longer
        # shows only the first candidate of a 2-model task.
        "selected_models": fixed_models,
        "material": task_material.payload(archive),
        "variants": task.get("variants") or [],
        "candidates": [row for row in (task.get("candidates") or []) if isinstance(row, dict)],
        "images": images,
        # Roles and rights are derived from the KB's own provenance and reported as such;
        # the studio shows them so nobody assumes an official export is an authorisation.
        # With several models each image already carries its own `product_id`, so no blanket
        # id is imposed (that would relabel the second model's photos as the first's).
        "assets": assets.resolve(images, product_id=(product_ref.get("product_id", "")
                                                     if len(fixed_models) <= 1 else "")),
        "copy": copy,
        "task": {key: task.get(key) for key in ("runner_version", "artifact_count")
                 if task.get(key) is not None},
        "capability": {
            "channel_configured": bool(os.getenv("IMAGE_API_KEY")),
            "reference_model": image_capabilities.reference_model(),
            "modes": ["img2img", "kb_original"],
            # 本进程是否支持把「新生成产品图」当参考图（image_id="gen:<generated_id>"）。
            # 前端据此放行/拦截：不支持的旧进程必须拦住——否则 gen: 会被 pick_reference
            # 当成"找不到的 id"而**静默回退到第一张知识库原图**，那是欺骗。
            "generated_reference": True,
            "generation_provenance": True,
            "note": "AI 生图以知识库核验原图作参考；也可直接使用原图，不调用生图模型。"},
        "notice": "工作室的产物是生成场景图，不是产品实物照；产品事实以知识库为准。",
    }


async def save_copy_async(db: Session, conversation: models.AgentConversation, *, content: dict,
                          artifact_id: str = "", base_revision: int | None = None,
                          product_id: str = "", note: str = "工作室改文案") -> dict:
    if not isinstance(content, dict) or not content:
        return {"ok": False, "error": "CONTENT_REQUIRED", "message": "没有可保存的文案内容"}
    catalog = await _catalog()
    state = db.get(models.AgentTaskState, conversation.id)
    task = dict(state.data or {}) if state else {}
    fixed = _fixed_product(task)
    resolved_id = product_id or fixed.get("product_id") or str(content.get("product_id") or "")
    if not artifact_id:
        # Default to the conversation's existing deliverable. Letting the saver fall back to
        # its own type lookup created a second artifact with revision 1, so a studio edit
        # silently forked the copy instead of versioning it.
        existing, _ = _latest_artifact(db, conversation.id)
        if existing is not None:
            artifact_id = existing.id
    ctx = _StudioContext(db, conversation, catalog,
                         last_search={"products": [{"product_id": fixed.get("product_id"),
                                                    "model": fixed.get("model")}]}
                         if fixed else {})
    if resolved_id:
        ctx.read_product_ids = [resolved_id]
    result = agent_runtime._save_artifact(ctx, {
        "artifact_id": artifact_id, "content": content, "base_revision": base_revision,
        "product_id": resolved_id, "note": note})
    return result


# 本次任务生成过的图：会话级、内存保存。图4 的「最近生成」用它；进程重启即清空是诚实的
# 边界（不假装是持久化历史），所以界面上写的是"本次任务会优先显示在这里"。
_GENERATED: dict[str, list[dict]] = {}
_GENERATED_LIMIT = 24


def remember_generated(conversation_id: str, rows: list[dict]) -> None:
    bucket = _GENERATED.setdefault(str(conversation_id), [])
    bucket[:0] = rows
    del bucket[_GENERATED_LIMIT:]


def generated_history(conversation_id: str) -> list[dict]:
    return list(_GENERATED.get(str(conversation_id)) or [])


def reset_generated() -> None:
    _GENERATED.clear()


# 重绘的语义 = 把这张图当初生成时的那条流程再走一遍，所以每张图必须记住它的来源：
# 参考图（image_id / access_url）、用户原始提示词、配方、模型、比例/质量/张数。
# generated_id 是这张生成图在本进程内的稳定标识，前端「新生成产品图」用它做参考图与重绘的键。
def generated_id(conversation_id: str, url: str, at: str, index: int) -> str:
    material = f"{conversation_id}|{url}|{at}|{index}".encode("utf-8")
    return hashlib.sha1(material).hexdigest()[:16]


def find_generated(conversation_id: str, generated_id_value: str) -> dict | None:
    """按 generated_id 找回一张本次生成的图；找不到就是找不到，不猜。"""
    wanted = str(generated_id_value or "")
    if not wanted:
        return None
    for row in generated_history(conversation_id):
        if str(row.get("generated_id") or "") == wanted:
            return row
    return None


def _copy_diff(before: str, after: str) -> dict:
    """从真实 diff 算出改动幅度与"改了什么"。

    实测：模型有时候只回 ``optimized``、不回 ``changes``，前端就变成"用户感知不到效果"。
    所以这里用**真实 diff** 兜底，并在返回里标明来源（``changes_source``），
    不把推导出来的话冒充成模型的自述。
    """
    import re

    def squeeze(text: str) -> str:
        return re.sub(r"\s+", "", str(text or ""))

    left, right = squeeze(before), squeeze(after)
    ratio = round(max(0.0, 1.0 - difflib.SequenceMatcher(None, left, right).ratio()), 3)
    before_lines = [line.strip() for line in str(before or "").splitlines() if line.strip()]
    after_lines = [line.strip() for line in str(after or "").splitlines() if line.strip()]
    derived: list[str] = []
    if before_lines and after_lines and squeeze(before_lines[0]) != squeeze(after_lines[0]):
        derived.append("重写开头：" + before_lines[0][:20] + " → " + after_lines[0][:20])
    if len(after_lines) > len(before_lines):
        derived.append("拆段加行：段落 " + str(len(before_lines)) + " → " +
                       str(len(after_lines)) + " 行")
    added = [line for line in after_lines if line not in before_lines]
    removed = [line for line in before_lines if line not in after_lines]
    if added:
        derived.append("新增表达：" + added[0][:40])
    if removed:
        derived.append("删除/合并表达：" + removed[0][:40])
    return {"changed_ratio": ratio, "lines_before": len(before_lines),
            "lines_after": len(after_lines), "derived_changes": derived}


async def optimize_copy(db: Session, conversation: models.AgentConversation, *,
                        content: str, instruction: str = "") -> dict:
    """文案优化：整篇重写表达，事实边界一条不松。

    用户实测反馈："点了优化好像没有效果"——上一版提示词只要求"只改表达，不新增事实"，
    模型就做成了"屏是触控的 → 屏幕支持触控"这种抠字眼。所以现在把**改动幅度**本身写成
    硬性要求（重写开头钩子、重排段落节奏、平台语感），同时把事实边界写得更细。

    只改表达，不改事实：不新增卖点、价格、参数或承诺。这一条写进提示词也写进返回里，
    因为"优化"最容易被做成"加料"。``changed_ratio`` 是对原文与优化文做字符级相似度得到的
    真实改动比例——如果模型只抠字眼，这个数字会很小，界面据此如实提示，不假装成功。
    """
    from .model import ModelError, chat_json_messages

    text = str(content or "").strip()
    if not text:
        return {"ok": False, "error": "EMPTY_COPY", "message": "没有可优化的文案"}
    material = await materials(db, conversation)
    state = db.get(models.AgentTaskState, conversation.id)
    surface = str((dict(state.data or {}) if state else {}).get("surface") or "未锁定")
    system = """你是华为 PC 的资深小红书爆款文案编辑。用户明确反馈"点了优化好像没有效果"，
所以这一版要**整篇重写**，不是抠字眼。把下面这段当成初稿，重新写一遍。

具体怎么做（篇幅和结构可以变，事实一个字都不能变）：
1. 重写开头钩子：前 12 个字就要有画面感或冲突感，让人愿意往下看。
2. 换语气与节奏：口语化、以短句为主，长短句交替，制造停顿和呼吸感。
3. 重排段落：一段只说一件事，每段 1–3 句；小标题可以单独成行，也可以改写成更口语的说法。
4. 平台语感按当前交付面来：小红书＝口语化 + 短句 + 适度 emoji（每段最多 1 个，可用可不用），
   其余平台保持该平台该有的语感；不要把口语改成公文腔。
5. 话题标签可以优化措辞与顺序，但原有标签一个都不能删，也不得添加没有根据的标签。
6. **凡是不承载参数的句子（铺垫、过渡、感受、适合谁、谁先别急、行动引导、结尾），
   都要换一种说法重写**；含参数的那一句，参数原样不动，但句子的铺垫、连接和收尾要改。
7. 幅度要求：至少一半以上的句子要换一种说法；只改几个词、或只把小标题换行，不算完成。

事实边界（一条都不能松，这是项目底线）：
- 只改表达，不新增事实：不得增加卖点、参数、价格、优惠、承诺或任何原文没有的信息。
- 保留原有的产品型号与已确认事实，不得替换或改写型号。
- 不得新增实测经历、口碑、排名、适配结论、销量、赠品或用户评价。
- 原文里的"约/最高/选配/仅某版本"等限定词必须原样保留，不得删掉，也不得放宽。
- 所有数字、单位、型号名、版本名、尺寸、时长、接口名，一个字符都不能改。
- 不得出现占位符、不得出现"某某"这类待填内容。

changes 必须给**至少 3 条**，写真实做过的较大改动（例如"重写开头钩子""把 3 个短句合并成有节奏的段落"
"给每段加了小标题分行的呼吸感"），不要拿"改了一个字"当成功。
kept 必须给**至少 2 条**，写清楚哪些事实、参数与限定词是刻意原样保留的。

只输出 JSON（字段顺序照抄）：
{"changes":["改了什么，每条半句话"],"kept":["刻意保留了什么"],"optimized":"优化后的完整文案"}"""
    user_content = (f"当前交付面：{surface}\n产品：{material['product'].get('model') or '未指定'}\n"
                    f"补充要求：{instruction or '（无）'}\n\n原文案：\n{text}")
    try:
        result, meta = chat_json_messages(
            system, [{"role": "user", "content": user_content}],
            schema_hint="只输出 JSON", model=None)
    except ModelError as error:
        return {"ok": False, "error": "MODEL_FAILED", "message": str(error)[:200]}
    optimized = str(result.get("optimized") or "").strip()
    if not optimized:
        # 实测（真实整篇小红书文案）：同一段输入偶发只回 changes/kept，optimized 是空的。
        # 这是模型抖动，不是文案本身的问题——重试一次；仍拿不到就如实报错，
        # 绝不拿原文冒充"已经优化过"。
        try:
            again, meta = chat_json_messages(
                system + "\n\n注意：这次的 optimized 必须给出完整文案本身，不能留空，"
                         "也不能只给改动清单。",
                [{"role": "user", "content": user_content}],
                schema_hint="只输出 JSON", model=None)
        except ModelError as error:
            return {"ok": False, "error": "MODEL_FAILED", "message": str(error)[:200]}
        optimized = str(again.get("optimized") or "").strip()
        if optimized:
            for key in ("changes", "kept"):
                if not (result.get(key) or []):
                    result[key] = again.get(key) or []
    if not optimized:
        return {"ok": False, "error": "EMPTY_RESULT",
                "message": "模型两次都没有返回优化后的文案，请再点一次「文案优化」"}
    optimized = optimized[:8000]
    # 改动幅度用**真实 diff**算：模型只抠字眼时这个数字会很小，界面要如实说出来。
    facts = _copy_diff(text, optimized)
    model_changes = [str(x)[:120] for x in (result.get("changes") or []) if str(x).strip()][:8]
    kept = [str(x)[:120] for x in (result.get("kept") or []) if str(x).strip()][:8]
    # 模型漏报 changes 时用真实 diff 兜底——不能让"改了什么"变成空白。
    changes = model_changes or facts["derived_changes"][:8]
    return {"ok": True, "optimized": optimized,
            "changes": changes,
            "changes_source": "model" if model_changes else "derived_from_diff",
            "kept": kept,
            "kept_source": "model" if kept else "not_reported",
            "changed_ratio": facts["changed_ratio"],
            "lines_before": facts["lines_before"], "lines_after": facts["lines_after"],
            "surface": surface, "usage": (meta or {}).get("usage") or {},
            "note": "整篇重写：语气、节奏、开头钩子与段落结构都重做；事实、参数与限定词原样保留，"
                    "未新增任何事实。如仍不够理想可再点一次，或回对话补充要求。"}


async def generate_image(db: Session, conversation: models.AgentConversation, *,
                         prompt: str, style: str = "", ratio: str = "", image_id: str = "",
                         size: str = "", model: str | None = None,
                         recipe_id: str = "", count: int = 1) -> dict:
    """Reference photo + prompt -> a generated scene image.

    Same guards as the agent tool: a verified KB image must exist, the model must have probe
    evidence for reference input, and the result is labelled as generated.
    """
    from .image_channel import (ImageChannelError, RATIO_PRESETS, build_prompt,
                                generate_from_product)

    material = await materials(db, conversation)
    images = material.get("images") or []
    # 参考图有两种：知识库核验原图（image_id）与「新生成产品图」。
    # 后者用 `gen:<generated_id>` 显式标明——不能拿一个知识库里不存在的 id 去 pick_reference：
    # pick_reference 在找不到 id 时会**回退到第一张原图**，那就成了"看着用了生成图、实际用了别的图"。
    generated_reference: dict | None = None
    if str(image_id).startswith("gen:"):
        generated_reference = find_generated(str(conversation.id), str(image_id)[4:])
        if generated_reference is None:
            return {"ok": False, "error": "REFERENCE_NOT_FOUND",
                    "message": "这张新生成图的记录已不在本次运行内（进程重启即清空），"
                               "请重新生成，或改选右侧的知识库原图"}
        chosen = {"image_id": str(image_id),
                  "caption": "本次生成的产品图（用作参考）",
                  "access_url": generated_reference.get("url") or "",
                  "source": {"kind": "generated_by_studio"}}
    else:
        # One selection rule for the studio and the agent tool, including the rights gate.
        chosen = assets.pick_reference(images, image_id=image_id)
    if chosen is None:
        if images and assets.require_rights():
            return {"ok": False, "error": "ASSET_RIGHTS_UNVERIFIED",
                    "message": "素材授权状态未经复核，按 IMAGE_REQUIRE_ASSET_RIGHTS 设置拒绝生成",
                    "assets": assets.resolve(images)["by_rights"]}
        return {"ok": False, "error": "NO_REFERENCE_IMAGE",
                "message": "本次创作还没有绑定的产品原图；先在对话里确认产品，或在工作室选择产品"}

    resolved = model or image_capabilities.reference_model()
    capability = image_capabilities.describe(resolved, True)
    if capability.get("supports") is False:
        return {"ok": False, "error": "CAPABILITY_UNAVAILABLE",
                "message": "该模型不支持参考图输入，已按探针证据拒绝调用",
                "capability": capability}

    token = str(chosen.get("access_url") or "").rstrip("/").split("/")[-1]
    stored = get_asset(token)
    if not stored:
        return {"ok": False, "error": "REFERENCE_EXPIRED",
                "message": "参考图的临时地址已过期（图片资产只在内存里，进程重启或超过有效期即失效），"
                           "请重新打开工作室材料或改选一张参考图"}
    body, mime = stored
    target_ratio = None
    if ratio and ratio != "kb":
        target_ratio = RATIO_PRESETS.get(ratio)
    # 风格配方：显式给了 recipe_id 就用它；否则在没有自定义提示词时，按本次对话锁定的交付面
    # 自动挑一个**已批准**的配方。recipes.select 在没有已批准配方时会拒绝，不会静默降级到
    # 未批准的配方——那正是未评审风格流到客户手里的方式。
    from . import recipes as recipe_lib
    recipe_used: dict | None = None
    text = (prompt or "").strip()
    if not text:
        if recipe_id:
            spec = recipe_lib.prompt_for(recipe_id)
            if not spec.get("ok"):
                return {"ok": False, "error": spec.get("error") or "RECIPE_NOT_FOUND",
                        "message": "指定的风格配方不存在", "recipe_id": recipe_id}
            if not spec.get("approved"):
                return {"ok": False, "error": "RECIPE_NOT_APPROVED", "recipe_id": recipe_id,
                        "message": "该配方未获批准，不得用于生产输出；如仅内部预览请显式传 "
                                   "allow_unapproved_recipe",
                        "note": "批准必须来自 releases.json 里记录的决定。"}
            text = spec["composed_prompt"]
            recipe_used = {"recipe_id": spec["recipe_id"], "approved": True,
                           "style_transform": spec.get("style_transform"),
                           "product_clause": spec["product_clause"]}
        else:
            surface = agent_runtime.get_conversation_surface(db, conversation).get("surface") or ""
            picked = recipe_lib.select(surface) if surface else {"ok": False,
                                                                 "error": "NO_SURFACE_LOCKED"}
            if picked.get("ok"):
                row = picked["recipe"]
                spec = recipe_lib.prompt_for(row["recipe_id"])
                text = spec["composed_prompt"]
                recipe_used = {"recipe_id": row["recipe_id"], "name": row.get("name"),
                               "approved": True, "surface": surface,
                               "style_transform": spec.get("style_transform"),
                               "product_clause": spec["product_clause"]}
            else:
                # 没有已批准配方：退回通用提示词，并把这件事如实带出去，不假装用了配方。
                text = build_prompt(
                    style=style, subject=material["product"].get("model", ""),
                    scene=material["product"].get("scenario", ""),
                    audience=material["product"].get("audience", ""))
                recipe_used = {"recipe_id": None, "approved": False,
                               "why_no_recipe": picked.get("error"),
                               "surface": surface or None}
    # Account for the paid attempt before submitting it. When no budget is configured this
    # records an observation instead of blocking (see cost_ledger.enforce for why, and how to
    # make the plan's rule binding).
    from . import cost_ledger
    booking = cost_ledger.record(
        db, request_id=str(conversation.id), estimated=None,
        idempotency_key="img:" + str(conversation.id) + ":" + str(int(time.time() * 1000)),
        conversation_id=conversation.id, purpose="studio_image",
        provider=image_capabilities.reference_model())
    if not booking.get("ok"):
        return {"ok": False, "error": booking.get("error") or "BILLING_BLOCKED",
                "message": str(booking.get("message") or "计费被拒绝"), "ledger": booking}
    # 计费记录写完后**立刻提交**，把写事务（SQLite 是单写者）在进上游之前放开。
    # 踩过的坑（并发实测，2026-10-23 上游恢复后）：ledger 只 flush 不 commit，事务于是横跨
    # 整个 30–100 秒的阻塞生图调用；4 张并发时后来者写自己的计费记录只能等 busy_timeout
    # 超时，抛 "database is locked" → HTTP 500，结果 4 张里只有 1 张成功。
    # 提交也符合账本语义：付费尝试在提交前就先落账，进程中途挂掉也留有记录。
    try:
        db.commit()
    except Exception as error:  # noqa: BLE001 - 落账失败必须如实中止，不静默继续
        db.rollback()
        return {"ok": False, "error": "LEDGER_COMMIT_FAILED",
                "message": "生图计费记录未能落库（" + type(error).__name__ +
                           "），已中止本次生图；没有把这次调用当成成功"}
    # 默认 120 秒：上游卡死时用户不该干等 5 分钟才看到失败。仍然走同一个环境变量入口，
    # 需要更长的可自己设 IMAGE_TIMEOUT_SECONDS，不在这里硬编码绕过配置。
    timeout = max(30, int(os.getenv("IMAGE_TIMEOUT_SECONDS", "120")))
    try:
        # 张数：界面上可选 1–4；之前这里写死 1，所以"张数"是假的。
        wanted = max(1, min(int(count or 1), 4))
        rendered, channel = await asyncio.to_thread(
            generate_from_product, body, text, mime or "image/png", resolved,
            size or None, wanted, target_ratio, timeout)
    except ImageChannelError as error:
        return {"ok": False, "error": "IMAGE_CHANNEL_ERROR", "message": str(error)[:220],
                "capability": capability}
    except Exception as error:  # noqa: BLE001 - reported, never disguised
        return {"ok": False, "error": "IMAGE_CHANNEL_ERROR",
                "message": type(error).__name__ + ": " + str(error)[:180]}
    out = []
    for raw in rendered[:4]:
        try:
            out.append({"access_url": store_asset(raw, "image/png"),
                        "kind": "generated_scene", "bytes": len(raw)})
        except Exception:  # noqa: BLE001
            continue
    if not out:
        return {"ok": False, "error": "NO_IMAGE_RETURNED", "message": "生图通道没有返回图片"}
    if booking.get("reservation_id") and not booking.get("priced"):
        # The provider reports token usage, so the observation can carry the measured charge
        # proxy instead of staying empty.
        cost_ledger.mark_unpriced(db, booking["reservation_id"], None,
                                  note="模型 " + resolved + "，用量由供应商 usage 字段提供")
    # 每张生成图都要记住"它是怎么来的"，重绘才有据可依：参考图（image_id / 地址）、
    # 用户当初输入的原始提示词（重绘要复现原始流程，所以不能用配方拼出来的最终提示词代替）、
    # 最终提示词（显示用）、配方、模型、比例、质量（由 size 体现）、张数、时间。
    at = time.strftime("%Y-%m-%d %H:%M")
    reference_url = str(chosen.get("access_url") or "")
    rows = []
    for index, row in enumerate(out or []):
        if not isinstance(row, dict):
            continue
        url = row.get("url") or row.get("access_url") or ""
        row_id = generated_id(str(conversation.id), url, at, index)
        # 直接把 generated_id 挂回响应里的图片对象：前端拿到就能立刻用作参考图/重绘的键，
        # 不必等「最近生成」再拉一次。
        row["generated_id"] = row_id
        rows.append({
            "generated_id": row_id,
            "url": url,
            "kind": "generated_scene",
            "origin": "studio",
            # prompt=用户原始输入（可能为空：那就由配方拼），composed_prompt=最终真正发出去的提示词
            "source_prompt": str(prompt or "").strip()[:4000],
            "composed_prompt": text[:4000],
            "prompt": text[:300],
            # 参考图来源
            "image_id": str(chosen.get("image_id") or ""),
            "reference_url": reference_url,
            "reference_kind": "generated" if generated_reference else "kb_original",
            # 生成参数
            "recipe_id": (recipe_used or {}).get("recipe_id"),
            "recipe_name": (recipe_used or {}).get("name"),
            "model": resolved,
            "ratio": ratio or "",
            "size": size or "auto",
            "quality": "high" if size else "standard",
            "count": wanted,
            "style": style or "",
            "at": at,
        })
    remember_generated(str(conversation.id), rows)
    return {"ok": True, "images": out, "channel": channel, "prompt": text,
            "requested_count": wanted, "size": size or "auto",
            # 溯源：本次用的哪个风格配方、是否已批准；没用配方也如实说明原因。
            "recipe": recipe_used,
            "capability": capability, "billing": {"priced": booking.get("priced"),
                                                  "state": booking.get("state"),
                                                  "note": booking.get("message", "")},
            "reference": {"image_id": chosen.get("image_id"), "caption": chosen.get("caption"),
                          "access_url": chosen.get("access_url"),
                          "kind": "generated" if generated_reference else "kb_original"},
            "notice": "以上为 AI 生成的场景图，不是产品实物照：机身比例、按键布局与接口位置由"
                      "模型重绘，不得作为产品外观、配色或接口的依据。"}