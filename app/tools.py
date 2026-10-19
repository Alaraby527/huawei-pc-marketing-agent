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
