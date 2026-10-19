"""Tool registry for the Rednote workspace agent.

The model is given these specs and may call them from the chat. Every tool
maps to a scoped service in `app.rednote`; nothing here bypasses workspace
checks or the approval gate.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from . import rednote
from .rednote import WorkspaceContext, WorkspaceError

TOOL_SPECS = [
    {"name": "consume_brief", "description": "从上游对话或手动产品型号生成不可变简报快照，返回 content_item_id",
     "parameters": {"type": "object", "properties": {
         "conversation_id": {"type": "string"}, "product_model": {"type": "string"},
         "objective": {"type": "string"}, "account_ref": {"type": "string"}}}},
    {"name": "select_trend", "description": "固定用户选定的主热点与利益点",
     "parameters": {"type": "object", "properties": {
         "content_item_id": {"type": "string"}, "title": {"type": "string"},
         "url": {"type": "string"}, "source": {"type": "string"},
         "scenario": {"type": "string"}, "benefits": {"type": "array", "items": {"type": "string"}}},
         "required": ["content_item_id", "title"]}},
    {"name": "create_directions", "description": "生成最多 6 个创意方向（至少 3 表达机制、3 视觉家族）",
     "parameters": {"type": "object", "properties": {
         "content_item_id": {"type": "string"}, "count": {"type": "integer"}},
         "required": ["content_item_id"]}},
    {"name": "create_board", "description": "选最多 3 个方向进入样张比较",
     "parameters": {"type": "object", "properties": {
         "content_item_id": {"type": "string"}, "direction_ids": {"type": "array", "items": {"type": "string"}}},
         "required": ["content_item_id", "direction_ids"]}},
    {"name": "render_samples", "description": "为比较板渲染封面/内页样张",
     "parameters": {"type": "object", "properties": {"board_id": {"type": "string"}},
                    "required": ["board_id"]}},
    {"name": "save_design", "description": "保存设计文档（原生 payload + sidecar + manifest）",
     "parameters": {"type": "object", "properties": {
         "content_item_id": {"type": "string"}, "native_payload": {"type": "object"},
         "sidecar": {"type": "object"}, "manifest": {"type": "object"},
         "document_id": {"type": "string"}, "expected_version": {"type": "integer"}},
         "required": ["content_item_id"]}},
    {"name": "generate_image", "description": "一键生图：用提示词 + 当前会话的产品原图做图生图，返回图片地址",
     "parameters": {"type": "object", "properties": {
         "conversation_id": {"type": "string"}, "image_id": {"type": "string"},
         "prompt": {"type": "string"}, "style": {"type": "string"}, "extra": {"type": "string"},
         "n": {"type": "integer"}}}},
    {"name": "list_design_styles", "description": "列出 16 个视觉家族与每家族封面/内页布局 ID",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "create_design_from_layout", "description": "用布局配方生成可编辑设计文档（Fabric payload + 语义绑定）",
     "parameters": {"type": "object", "properties": {
         "content_item_id": {"type": "string"}, "layout_id": {"type": "string"},
         "bindings": {"type": "object"}, "product_name": {"type": "string"}},
         "required": ["content_item_id", "layout_id"]}},
    {"name": "check_design_document", "description": "对设计文档做尺寸/字体/文字越界/占位图检查",
     "parameters": {"type": "object", "properties": {"document_id": {"type": "string"}},
                    "required": ["document_id"]}},
    {"name": "submit_review", "description": "提交当前设计版本审核",
     "parameters": {"type": "object", "properties": {
         "content_item_id": {"type": "string"}, "design_document_id": {"type": "string"}},
         "required": ["content_item_id", "design_document_id"]}},
    {"name": "approve", "description": "批准当前待审版本（需人工角色动作）",
     "parameters": {"type": "object", "properties": {
         "content_item_id": {"type": "string"}, "revision_id": {"type": "string"},
         "account_ref": {"type": "string"}}, "required": ["content_item_id", "revision_id"]}},
    {"name": "plan_publish", "description": "为已批准版本创建发布计划（manual 或 connector）",
     "parameters": {"type": "object", "properties": {
         "content_item_id": {"type": "string"}, "revision_id": {"type": "string"},
         "account_ref": {"type": "string"}, "mode": {"type": "string"}},
         "required": ["content_item_id", "revision_id"]}},
    {"name": "execute_publish", "description": "执行人工发布路径，生成发布包；连接器路径会返回待联调",
     "parameters": {"type": "object", "properties": {"plan_id": {"type": "string"}},
                    "required": ["plan_id"]}},
    {"name": "report_publish", "description": "回填已发布笔记链接，状态为待核对",
     "parameters": {"type": "object", "properties": {
         "plan_id": {"type": "string"}, "url": {"type": "string"}, "platform_note_id": {"type": "string"}},
         "required": ["plan_id", "url"]}},
    {"name": "analytics_overview", "description": "读取运营监控合同与空态（不返回伪造数据）",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "list_publish_plans", "description": "列出发布计划与已回填笔记",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "list_alerts", "description": "列出告警（M1 为空态合同）",
     "parameters": {"type": "object", "properties": {}}},
]

TOOL_NAMES = {spec["name"] for spec in TOOL_SPECS}


def _clip(value, depth=0):
    if depth > 4:
        return "…"
    if isinstance(value, list):
        return [_clip(item, depth + 1) for item in value[:6]]
    if isinstance(value, dict):
        return {key: _clip(item, depth + 1) for key, item in list(value.items())[:20]}
    if isinstance(value, str):
        return value[:400]
    return value


def dispatch(db: Session, ctx: WorkspaceContext, name: str, args: dict) -> dict:
    """Execute one tool call with workspace scoping; errors are returned as data."""
    if name not in TOOL_NAMES:
        raise WorkspaceError("CAPABILITY_DISABLED", f"未知工具：{name}", 400)
    args = args or {}
    try:
        if name == "consume_brief":
            payload = rednote.BriefConsume(**{key: args[key] for key in
                                              ("conversation_id", "product_model", "objective", "account_ref")
                                              if key in args})
            return _clip(rednote.consume_brief(db, ctx, payload))
        if name == "select_trend":
            payload = rednote.TrendSelectionIn(
                content_item_id=args["content_item_id"],
                main_topic={"title": args.get("title", ""), "url": args.get("url", ""),
                            "source": args.get("source", "")},
                aux_topics=[], scenario=args.get("scenario", ""),
                benefits=args.get("benefits", []), evidence_ids=[args["url"]] if args.get("url") else [])
            return _clip(rednote.create_trend_selection(db, ctx, payload))
        if name == "create_directions":
            payload = rednote.DirectionsIn(content_item_id=args["content_item_id"],
                                           count=int(args.get("count", 6)))
            return _clip(rednote.create_creative_directions(db, ctx, payload))
        if name == "create_board":
            payload = rednote.BoardIn(content_item_id=args["content_item_id"],
                                      direction_ids=args.get("direction_ids", [])[:3])
            return _clip(rednote.create_creative_board(db, ctx, payload))
        if name == "render_samples":
            return _clip(rednote.style_preview(db, ctx, args["board_id"]))
        if name == "save_design":
            if args.get("document_id"):
                payload = rednote.DesignDocumentPatch(expected_version=int(args.get("expected_version", 1)),
                                                      native_payload=args.get("native_payload"),
                                                      sidecar=args.get("sidecar"),
                                                      manifest=args.get("manifest"))
                return _clip(rednote.patch_design_document(db, ctx, args["document_id"], payload))
            payload = rednote.DesignDocumentIn(content_item_id=args["content_item_id"],
                                               native_payload=args.get("native_payload", {}),
                                               sidecar=args.get("sidecar", {}),
                                               manifest=args.get("manifest", {}))
            return _clip(rednote.create_design_document(db, ctx, payload))
        if name == "generate_image":
            from . import image_channel, image_edit
            from .conversation import service as conversation_service
            from .retrieval import get_asset, store_asset
            session = conversation_service.sessions.get(str(args.get("conversation_id") or ""))
            handoff = session.handoff_cache if session else None
            if not handoff or not handoff.get("images"):
                return {"error": "EVIDENCE_REQUIRED", "message": "需要先有产品原图，请先完成选品与核验"}
            images = handoff["images"]
            image = next((row for row in images if row.get("image_id") == args.get("image_id")), images[0])
            token = (image.get("access_url") or "").rstrip("/").split("/")[-1]
            stored = get_asset(token)
            if not stored:
                return {"error": "SOURCE_UNAVAILABLE", "message": "产品图片临时地址已过期，请刷新材料"}
            slots = session.slots.model_dump(mode="json")
            facts = [str(fact.get("text", "")).partition("：")[0] for fact in handoff.get("facts", [])[:3]]
            draft_title = (session.draft or {}).get("title", "")
            style = args.get("style", "clean_desk")
            prompt = args.get("prompt") or image_channel.build_prompt(
                style=style, subject=draft_title or slots.get("instructions", "").split("\n")[0],
                scene=slots.get("scenario", ""), audience=slots.get("audience", ""),
                facts=facts, extra=args.get("extra", ""))
            if image_channel.configured():
                results = image_channel.generate_from_product(stored[0], prompt, stored[1],
                                                              None, "1024x1024",
                                                              int(args.get("n", 1) or 1))
                channel = "image_api"
            elif image_edit.has_alpha(stored[0]):
                results = image_edit.generate_scene_image(stored[0], style, args.get("prompt", ""))
                channel = "dashscope-scene"
            else:
                results = image_edit.edit_image(stored[0], prompt, stored[1], None,
                                                int(args.get("n", 1) or 1), None)
                channel = "dashscope-edit"
            return _clip({"images": [store_asset(content, "image/png") for content in results],
                          "channel": channel, "prompt": prompt,
                          "note": "生成图只作氛围背景，不是产品实拍"})
        if name == "list_design_styles":
            from . import design_library
            return _clip({"families": design_library.styles(), "count": len(design_library.RECIPES)})
        if name == "create_design_from_layout":
            from . import design_library
            built = design_library.build_document(args["layout_id"], args.get("bindings") or {},
                                                  str(args.get("product_name", "")))
            payload = rednote.DesignDocumentIn(content_item_id=args["content_item_id"],
                                               engine=built["engine"], engine_version=built["engine_version"],
                                               native_payload=built["native_payload"],
                                               sidecar=built["sidecar"], manifest=built["manifest"])
            return _clip(rednote.create_design_document(db, ctx, payload))
        if name == "check_design_document":
            from . import design_library
            from .models import DesignDocument
            row = rednote._require(db, ctx, DesignDocument, args["document_id"])
            return _clip(design_library.check_document({"native_payload": row.native_payload,
                                                        "sidecar": row.sidecar, "manifest": row.manifest}))
        if name == "submit_review":
            payload = rednote.ReviewSubmission(content_item_id=args["content_item_id"],
                                               design_document_id=args["design_document_id"])
            return _clip(rednote.submit_review(db, ctx, payload))
        if name == "approve":
            payload = rednote.ApprovalIn(content_item_id=args["content_item_id"],
                                         revision_id=args["revision_id"],
                                         account_ref=args.get("account_ref", ""),
                                         approved_by=ctx.actor)
            return _clip(rednote.approve(db, ctx, payload))
        if name == "plan_publish":
            payload = rednote.PublishPlanIn(content_item_id=args["content_item_id"],
                                            revision_id=args["revision_id"],
                                            account_ref=args.get("account_ref", ""),
                                            mode=args.get("mode", "manual"))
            return _clip(rednote.create_publish_plan(db, ctx, payload))
        if name == "execute_publish":
            return _clip(rednote.execute_publish(db, ctx, args["plan_id"]))
        if name == "report_publish":
            payload = rednote.ManualResultIn(url=args["url"], platform_note_id=args.get("platform_note_id", ""))
            return _clip(rednote.manual_result(db, ctx, args["plan_id"], payload))
        if name == "analytics_overview":
            return _clip(rednote.overview(db, ctx))
        if name == "list_publish_plans":
            return _clip(rednote.list_publish_plans(db, ctx))
        if name == "list_alerts":
            return _clip(rednote.list_alerts(db, ctx))
    except WorkspaceError as error:
        return {"error": error.code, "message": error.message}
    raise WorkspaceError("CAPABILITY_DISABLED", f"工具未实现：{name}", 400)
