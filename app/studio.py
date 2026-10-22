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
    """The product settled earlier in the conversation, if there is one."""
    for candidate in (task.get("candidates") or []):
        if isinstance(candidate, dict) and candidate.get("product_id"):
            return {"product_id": candidate["product_id"],
                    "model": candidate.get("model") or "",
                    "facts_count": candidate.get("facts_count")}
    return {}


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
    images: list[dict] = []
    if product is not None:
        try:
            rows, _ = await verified_images(catalog, product, Platform.XIAOHONGSHU,
                                            ["屏幕", "接口", "尺寸与重量", "电池"])
            # `source` is carried through deliberately: it holds the provenance that the
            # asset resolver reads. Dropping it degraded every rights status to "unknown",
            # losing the more truthful "provenance recorded, terms unreviewed".
            images = [{"image_id": agent_runtime._image_field(row, "image_id"),
                       "caption": agent_runtime._image_field(row, "caption"),
                       "access_url": agent_runtime._image_field(row, "access_url"),
                       "model_verified": agent_runtime._image_field(row, "model_verified"),
                       "verification": _raw_field(row, "verification"),
                       "source": _raw_field(row, "source")}
                      for row in (rows or [])]
        except Exception:  # noqa: BLE001 - no image is a normal state, reported as empty
            images = []
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
    return {
        "conversation_id": conversation.id,
        "title": summary.get("title", ""),
        # Always the same keys, even with nothing pinned: a client should not have to guess
        # whether a missing key means "no product" or "the field was dropped".
        "product": {"product_id": product_ref.get("product_id", ""),
                    "model": (product.model if product is not None
                              else product_ref.get("model", "")),
                    "facts_count": product_ref.get("facts_count"),
                    "audience": str(task.get("audience") or ""),
                    "scenario": str(task.get("scenario") or "")},
        "candidates": [row for row in (task.get("candidates") or []) if isinstance(row, dict)],
        "images": images,
        # Roles and rights are derived from the KB's own provenance and reported as such;
        # the studio shows them so nobody assumes an official export is an authorisation.
        "assets": assets.resolve(images, product_id=product_ref.get("product_id", "")),
        "copy": copy,
        "task": {key: task.get(key) for key in ("runner_version", "artifact_count")
                 if task.get(key) is not None},
        "capability": {
            "channel_configured": bool(os.getenv("IMAGE_API_KEY")),
            "reference_model": image_capabilities.reference_model(),
            "modes": ["img2img", "kb_original"],
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


async def optimize_copy(db: Session, conversation: models.AgentConversation, *,
                        content: str, instruction: str = "") -> dict:
    """文案优化：在保留事实边界的前提下改写文案。

    只改表达，不改事实：不新增卖点、价格、参数或承诺。这一条写进提示词也写进返回里，
    因为"优化"最容易被做成"加料"。
    """
    from .model import ModelError, chat_json_messages

    text = str(content or "").strip()
    if not text:
        return {"ok": False, "error": "EMPTY_COPY", "message": "没有可优化的文案"}
    material = await materials(db, conversation)
    state = db.get(models.AgentTaskState, conversation.id)
    surface = str((dict(state.data or {}) if state else {}).get("surface") or "未锁定")
    system = """你是华为 PC 的文案编辑。请优化下面这段文案。
硬性要求：
1. 只改表达，不新增事实：不得增加卖点、参数、价格、优惠、承诺或任何原文没有的信息。
2. 保留原有的产品型号与已确认事实，不得替换或改写型号。
3. 不得出现占位符、不得出现"某某"这类待填内容。
4. 保持原有交付面的语感（当前交付面会告诉你）。不要把口语改成公文腔。
只输出 JSON：{"optimized":"优化后的完整文案","changes":["改了什么，每条半句话"],"kept":["刻意保留了什么"]}"""
    try:
        result, meta = chat_json_messages(
            system, [{"role": "user",
                      "content": f"当前交付面：{surface}\n产品：{material['product'].get('model') or '未指定'}\n"
                                 f"补充要求：{instruction or '（无）'}\n\n原文案：\n{text}"}],
            schema_hint="只输出 JSON", model=None)
    except ModelError as error:
        return {"ok": False, "error": "MODEL_FAILED", "message": str(error)[:200]}
    return {"ok": True, "optimized": str(result.get("optimized") or "")[:6000],
            "changes": [str(x)[:120] for x in (result.get("changes") or [])][:8],
            "kept": [str(x)[:120] for x in (result.get("kept") or [])][:8],
            "surface": surface, "usage": (meta or {}).get("usage") or {},
            "note": "只改表达不改事实；新增事实请回到对话确认。"}


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
                "message": "产品原图临时地址已过期，请重新打开工作室材料"}
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
    timeout = max(30, int(os.getenv("IMAGE_TIMEOUT_SECONDS", "300")))
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
    remember_generated(str(conversation.id), [
        {"url": row.get("url") or row.get("access_url") or "",
         "prompt": text[:300], "model": resolved,
         "recipe_id": (recipe_used or {}).get("recipe_id"),
         "recipe_name": (recipe_used or {}).get("name"),
         "at": time.strftime("%Y-%m-%d %H:%M")}
        for row in (out or []) if isinstance(row, dict)])
    return {"ok": True, "images": out, "channel": channel, "prompt": text,
            "requested_count": wanted, "size": size or "auto",
            # 溯源：本次用的哪个风格配方、是否已批准；没用配方也如实说明原因。
            "recipe": recipe_used,
            "capability": capability, "billing": {"priced": booking.get("priced"),
                                                  "state": booking.get("state"),
                                                  "note": booking.get("message", "")},
            "reference": {"image_id": chosen.get("image_id"), "caption": chosen.get("caption"),
                          "access_url": chosen.get("access_url")},
            "notice": "以上为 AI 生成的场景图，不是产品实物照：机身比例、按键布局与接口位置由"
                      "模型重绘，不得作为产品外观、配色或接口的依据。"}