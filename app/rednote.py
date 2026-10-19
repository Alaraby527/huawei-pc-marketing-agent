"""Rednote workspace services (M1).

Scope: brief handoff, structured trend selection, creative directions,
sample boards, design documents, review/approval and the manual publish path.
Publishing through the connector, analytics and comments are registered as
pending integrations and never faked here.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models
from .posters import render_page


EXPRESSION_IDS = [f"C{i:02d}" for i in range(1, 13)]
FAMILY_IDS = [f"V{i:02d}" for i in range(1, 17)]
NARRATIVE_IDS = [f"N{i:02d}" for i in range(1, 9)]
PRODUCT_MODE_IDS = [f"P{i:02d}" for i in range(1, 7)]

# Curated default combinations: six directions covering at least three
# expression mechanisms and three visual families (D03).
DIRECTION_BLUEPRINTS = [
    ("C02", "V06", "N01", "P02", "反差幽默", "把期待与现实的反差放在第一屏"),
    ("C03", "V01", "N03", "P01", "生活切片", "用一天里的真实场景串起产品"),
    ("C05", "V08", "N04", "P02", "场景问题解决", "从任务阻碍自然引出已确认利益点"),
    ("C09", "V05", "N05", "P03", "清单收藏", "用准备清单把场景讲清楚"),
    ("C08", "V15", "N07", "P01", "决策参考", "只引用有来源的事实做取舍说明"),
    ("C10", "V02", "N08", "P05", "产品意象", "用视觉隐喻表达设计感，标注为示意"),
]


class WorkspaceError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


@dataclass
class WorkspaceContext:
    workspace_id: str = "default"
    actor: str = "local-operator"
    roles: list[str] = field(default_factory=lambda: ["owner"])


def ensure_workspace(db: Session, workspace_id: str) -> None:
    if not db.get(models.Workspace, workspace_id):
        db.add(models.Workspace(id=workspace_id, name=workspace_id))
        db.flush()


def audit(db: Session, ctx: WorkspaceContext, action: str, object_type: str, object_id: str, detail: dict | None = None) -> None:
    db.add(models.AuditLog(workspace_id=ctx.workspace_id, actor=ctx.actor, action=action,
                           object_type=object_type, object_id=object_id, detail=detail or {}))


def data_dir() -> Path:
    return Path(os.getenv("REDNOTE_DATA_DIR", "data"))


def digest(value) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def _require(db: Session, ctx: WorkspaceContext, model, object_id: str):
    row = db.get(model, object_id)
    if not row or row.workspace_id != ctx.workspace_id:
        raise WorkspaceError("WORKSPACE_ACCESS_DENIED", "对象不存在或不属于当前工作区", 404)
    return row


# ---------------------------------------------------------------- schemas

class BriefConsume(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation_id: str | None = Field(default=None, max_length=64)
    account_ref: str = Field(default="", max_length=120)
    objective: str = Field(default="", max_length=200)
    constraints: list[str] = Field(default_factory=list, max_length=20)
    product_model: str = Field(default="", max_length=200)
    product_configuration: str = Field(default="", max_length=300)


class TrendSelectionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content_item_id: str = Field(min_length=1, max_length=32)
    main_topic: dict
    aux_topics: list[dict] = Field(default_factory=list, max_length=2)
    scenario: str = Field(default="", max_length=300)
    benefits: list[str] = Field(default_factory=list, max_length=10)
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)


class DirectionsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content_item_id: str = Field(min_length=1, max_length=32)
    count: int = Field(default=6, ge=1, le=6)


class BoardIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content_item_id: str = Field(min_length=1, max_length=32)
    direction_ids: list[str] = Field(default_factory=list, max_length=3)


class DesignDocumentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content_item_id: str = Field(min_length=1, max_length=32)
    engine: str = Field(default="vue-fabric-editor", max_length=40)
    engine_version: str = Field(default="", max_length=40)
    native_payload: dict = Field(default_factory=dict)
    sidecar: dict = Field(default_factory=dict)
    manifest: dict = Field(default_factory=dict)


class DesignDocumentPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)
    native_payload: dict | None = None
    sidecar: dict | None = None
    manifest: dict | None = None


class ReviewSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content_item_id: str = Field(min_length=1, max_length=32)
    design_document_id: str = Field(min_length=1, max_length=32)


class ApprovalIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content_item_id: str = Field(min_length=1, max_length=32)
    revision_id: str = Field(min_length=1, max_length=32)
    account_ref: str = Field(default="", max_length=120)
    approved_window: str = Field(default="", max_length=80)
    approved_by: str = Field(default="local-operator", max_length=120)


class PublishPlanIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content_item_id: str = Field(min_length=1, max_length=32)
    revision_id: str = Field(min_length=1, max_length=32)
    account_ref: str = Field(default="", max_length=120)
    scheduled_at: datetime | None = None
    mode: str = Field(default="manual", pattern="^(manual|connector)$")


class ManualResultIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=1, max_length=1000)
    platform_note_id: str = Field(default="", max_length=120)
    reported_at: datetime | None = None


# ---------------------------------------------------------------- services

def consume_brief(db: Session, ctx: WorkspaceContext, payload: BriefConsume) -> dict:
    from .conversation import service as conversation_service

    brief: dict = {
        "schema_version": "1.0",
        "source_workspace": "huawei-pc-marketing-agent",
        "target_workspace": ctx.workspace_id,
        "authorized_actor": ctx.actor,
        "account_ref": payload.account_ref,
        "objective": payload.objective,
        "constraints": payload.constraints,
        "products": [],
        "facts": [],
        "images": [],
        "scenarios": [],
        "audiences": [],
        "benefits": [],
        "source_conversation_id": payload.conversation_id,
        "source_revision": None,
    }
    handoff = None
    session = conversation_service.sessions.get(payload.conversation_id) if payload.conversation_id else None
    if payload.conversation_id:
        if not session:
            raise WorkspaceError("SOURCE_UNAVAILABLE", "上游会话不存在或已过期", 404)
        handoff = session.handoff_cache
        draft = session.draft or {}
        brief["source_revision"] = session.revision
        slots = session.slots.model_dump(mode="json")
        brief["scenarios"] = [slots.get("scenario")] if slots.get("scenario") else []
        brief["audiences"] = [slots.get("audience")] if slots.get("audience") else []
        if slots.get("objective"):
            brief["objective"] = brief["objective"] or slots["objective"]
        if slots.get("hotspot"):
            brief["benefits"].append(slots["hotspot"])
        if handoff:
            product = (handoff.get("materials") or {}).get("product") or {}
            brief["products"].append({
                "model_name": product.get("model_name", ""),
                "configuration": product.get("configuration", ""),
                "product_id": product.get("product_id", ""),
            })
            for fact in handoff.get("facts", []):
                brief["facts"].append({
                    "fact_id": fact.get("fact_id", ""),
                    "text": fact.get("text", ""),
                    "source": (fact.get("source") or {}).get("title", ""),
                    "verification_status": "verified",
                })
            for image in handoff.get("images", []):
                brief["images"].append({
                    "image_id": image.get("image_id", ""),
                    "caption": image.get("caption", ""),
                    "access_url": image.get("access_url", ""),
                    "verification_status": "model_verified" if image.get("model_verified") else "unverified",
                })
        if draft.get("pages"):
            brief["storyboard_seed"] = draft["pages"]
    if payload.product_model:
        brief["products"].append({"model_name": payload.product_model,
                                  "configuration": payload.product_configuration, "product_id": ""})
    if not brief["products"]:
        raise WorkspaceError("EVIDENCE_REQUIRED", "简报缺少产品信息；请从上游方案带入或提供产品型号", 422)
    brief["valid_until"] = (datetime.now(timezone.utc) + timedelta(days=14)).isoformat()

    handoff_row = models.BriefHandoff(workspace_id=ctx.workspace_id,
                                      source_conversation_id=payload.conversation_id,
                                      source_revision=brief["source_revision"], payload=brief)
    db.add(handoff_row)
    db.flush()
    snapshot = models.BriefSnapshot(workspace_id=ctx.workspace_id, handoff_id=handoff_row.id,
                                    content_hash=digest(brief), payload=brief)
    db.add(snapshot)
    db.flush()
    product_row = None
    if brief["products"]:
        first = brief["products"][0]
        product_row = models.Product(workspace_id=ctx.workspace_id,
                                     model_name=first.get("model_name", ""),
                                     configuration=first.get("configuration", ""))
        db.add(product_row)
        db.flush()
        for fact in brief["facts"]:
            db.add(models.ProductClaim(workspace_id=ctx.workspace_id, product_id=product_row.id,
                                       claim_id=fact.get("fact_id", ""), text=fact.get("text", ""),
                                       evidence_id=fact.get("source", ""), verification_status="verified"))
    item = models.ContentItem(workspace_id=ctx.workspace_id, brief_snapshot_id=snapshot.id,
                              product_id=product_row.id if product_row else None,
                              account_ref=payload.account_ref, objective=brief["objective"],
                              status="BRIEF")
    db.add(item)
    db.flush()
    audit(db, ctx, "brief.consume", "content_item", item.id, {"snapshot_id": snapshot.id})
    return {"snapshot_id": snapshot.id, "content_item_id": item.id, "content_hash": snapshot.content_hash,
            "brief": brief}


def create_trend_selection(db: Session, ctx: WorkspaceContext, payload: TrendSelectionIn) -> dict:
    item = _require(db, ctx, models.ContentItem, payload.content_item_id)
    row = models.TrendSelection(workspace_id=ctx.workspace_id, content_item_id=item.id,
                                main_topic=payload.main_topic, aux_topics=payload.aux_topics,
                                scenario=payload.scenario, benefits=payload.benefits,
                                evidence_ids=payload.evidence_ids)
    db.add(row)
    item.status = "RESEARCH_READY"
    db.flush()
    audit(db, ctx, "trend.select", "content_item", item.id, {"selection_id": row.id})
    return {"selection_id": row.id, "content_item_id": item.id}


def _direction_copy(blueprint, item, brief, selection) -> dict:
    expression, family, narrative, mode, label, why = blueprint
    product = (brief.get("products") or [{}])[0].get("model_name", "该型号")
    topic = (selection.main_topic or {}).get("title", "") if selection else ""
    scene = (selection.scenario if selection else "") or "日常使用"
    benefit = (selection.benefits[0] if selection and selection.benefits else "已确认利益点")
    return {
        "expression_id": expression, "family_id": family, "narrative_id": narrative,
        "product_mode_id": mode,
        "title": f"{label}｜{product}的{scene}"[:60],
        "hook": f"用「{label}」切入{topic or scene}，第一屏先给出反差或问题。",
        "why_product": f"产品只在能落到「{benefit}」时出现；引用简报中的已核实事实，不写未确认参数。",
        "risk": "热点可能过期或与产品关联偏弱；发布前需复核时效与事实来源。" if topic else "无热点时按常青选题执行。",
        "payload": {"label": label, "why": why, "topic": topic, "scene": scene, "benefit": benefit},
    }


def create_creative_directions(db: Session, ctx: WorkspaceContext, payload: DirectionsIn) -> dict:
    item = _require(db, ctx, models.ContentItem, payload.content_item_id)
    snapshot = db.get(models.BriefSnapshot, item.brief_snapshot_id) if item.brief_snapshot_id else None
    brief = snapshot.payload if snapshot else {}
    selection = db.scalars(select(models.TrendSelection).where(
        models.TrendSelection.workspace_id == ctx.workspace_id,
        models.TrendSelection.content_item_id == item.id).order_by(models.TrendSelection.created_at.desc())).first()
    existing = db.scalars(select(models.CreativeDirection).where(
        models.CreativeDirection.workspace_id == ctx.workspace_id,
        models.CreativeDirection.content_item_id == item.id)).all()
    for row in existing:
        db.delete(row)
    db.flush()
    rows = []
    for index, blueprint in enumerate(DIRECTION_BLUEPRINTS[:payload.count], 1):
        copy = _direction_copy(blueprint, item, brief, selection)
        row = models.CreativeDirection(workspace_id=ctx.workspace_id, content_item_id=item.id, ordinal=index, **copy)
        db.add(row)
        rows.append(row)
    item.status = "IDEAS_READY"
    db.flush()
    audit(db, ctx, "directions.create", "content_item", item.id, {"count": len(rows)})
    families = {row.family_id for row in rows}
    expressions = {row.expression_id for row in rows}
    return {"content_item_id": item.id, "directions": [_direction_out(row) for row in rows],
            "diversity": {"families": len(families), "expressions": len(expressions),
                          "meets_target": len(families) >= 3 and len(expressions) >= 3}}


def _direction_out(row: models.CreativeDirection) -> dict:
    return {"id": row.id, "ordinal": row.ordinal, "expression_id": row.expression_id,
            "family_id": row.family_id, "narrative_id": row.narrative_id,
            "product_mode_id": row.product_mode_id, "title": row.title, "hook": row.hook,
            "why_product": row.why_product, "risk": row.risk, "payload": row.payload}


def create_creative_board(db: Session, ctx: WorkspaceContext, payload: BoardIn) -> dict:
    item = _require(db, ctx, models.ContentItem, payload.content_item_id)
    if not payload.direction_ids:
        raise WorkspaceError("EVIDENCE_REQUIRED", "请至少选择一个创意方向", 422)
    directions = [db.get(models.CreativeDirection, direction_id) for direction_id in payload.direction_ids]
    for row in directions:
        if not row or row.workspace_id != ctx.workspace_id or row.content_item_id != item.id:
            raise WorkspaceError("WORKSPACE_ACCESS_DENIED", "创意方向不属于当前内容", 403)
    board = models.CreativeBoard(workspace_id=ctx.workspace_id, content_item_id=item.id,
                                 direction_ids=[row.id for row in directions], status="open")
    db.add(board)
    item.status = "IDEA_SELECTED"
    db.flush()
    return {"board_id": board.id, "direction_ids": board.direction_ids}


def style_preview(db: Session, ctx: WorkspaceContext, board_id: str) -> dict:
    board = _require(db, ctx, models.CreativeBoard, board_id)
    item = _require(db, ctx, models.ContentItem, board.content_item_id)
    snapshot = db.get(models.BriefSnapshot, item.brief_snapshot_id) if item.brief_snapshot_id else None
    product = ((snapshot.payload if snapshot else {}).get("products") or [{}])[0].get("model_name", "HUAWEI MateBook")
    candidates = []
    for direction_id in board.direction_ids:
        direction = db.get(models.CreativeDirection, direction_id)
        if not direction:
            continue
        for role in ("cover", "inner"):
            draft = {"pages": [{
                "role": "hook" if role == "cover" else "proof",
                "title": direction.title if role == "cover" else direction.hook[:40],
                "text": direction.why_product if role == "cover" else direction.risk,
                "fact_ids": [], "image_id": "",
            }]}
            handoff = {"images": [], "facts": [], "product_identity": {"model_name": product}}
            content = render_page(draft, handoff, 0)
            asset = _store_asset(db, ctx, content, f"{direction.id}-{role}.png", "template-preview")
            candidate = models.PreviewCandidate(workspace_id=ctx.workspace_id, board_id=board.id,
                                                direction_id=direction.id, role=role, asset_id=asset.id,
                                                payload={"placeholder": True, "note": "模板样张，未使用真实产品图"})
            db.add(candidate)
            candidates.append(candidate)
    db.flush()
    audit(db, ctx, "board.preview", "creative_board", board.id, {"candidates": len(candidates)})
    return {"board_id": board.id, "candidates": [
        {"id": row.id, "direction_id": row.direction_id, "role": row.role,
         "asset_url": f"/api/v1/workspaces/{ctx.workspace_id}/apps/rednote/assets/{row.asset_id}"}
        for row in candidates]}


def _store_asset(db: Session, ctx: WorkspaceContext, content: bytes, filename: str, source: str) -> models.Asset:
    folder = data_dir() / "workspaces" / ctx.workspace_id / "assets"
    folder.mkdir(parents=True, exist_ok=True)
    sha = hashlib.sha256(content).hexdigest()
    path = folder / f"{sha[:16]}-{filename}"
    path.write_bytes(content)
    asset = models.Asset(workspace_id=ctx.workspace_id, kind="image", filename=filename,
                         mime="image/png", sha256=sha, path=str(path), source=source,
                         license_note="自研模板渲染")
    db.add(asset)
    db.flush()
    return asset


def get_asset_path(db: Session, ctx: WorkspaceContext, asset_id: str) -> Path:
    asset = _require(db, ctx, models.Asset, asset_id)
    path = Path(asset.path).resolve()
    root = (data_dir() / "workspaces" / ctx.workspace_id / "assets").resolve()
    if not str(path).startswith(str(root)) or not path.exists():
        raise WorkspaceError("WORKSPACE_ACCESS_DENIED", "资产不可读", 403)
    return path


def create_design_document(db: Session, ctx: WorkspaceContext, payload: DesignDocumentIn) -> dict:
    item = _require(db, ctx, models.ContentItem, payload.content_item_id)
    revision_hash = digest({"native": payload.native_payload, "sidecar": payload.sidecar,
                            "manifest": payload.manifest})
    row = models.DesignDocument(workspace_id=ctx.workspace_id, content_item_id=item.id,
                                engine=payload.engine, engine_version=payload.engine_version,
                                revision_hash=revision_hash, native_payload=payload.native_payload,
                                sidecar=payload.sidecar, manifest=payload.manifest)
    db.add(row)
    item.status = "EDITABLE"
    db.flush()
    return _design_out(row)


def patch_design_document(db: Session, ctx: WorkspaceContext, document_id: str,
                          payload: DesignDocumentPatch) -> dict:
    row = _require(db, ctx, models.DesignDocument, document_id)
    if payload.expected_version != row.version:
        raise WorkspaceError("STALE_REVISION", "设计文档已被其他会话修改，请刷新后重试", 409)
    native = payload.native_payload if payload.native_payload is not None else row.native_payload
    sidecar = payload.sidecar if payload.sidecar is not None else row.sidecar
    manifest = payload.manifest if payload.manifest is not None else row.manifest
    row.version += 1
    row.native_payload, row.sidecar, row.manifest = native, sidecar, manifest
    row.revision_hash = digest({"native": native, "sidecar": sidecar, "manifest": manifest})
    db.flush()
    return _design_out(row)


def _design_out(row: models.DesignDocument) -> dict:
    return {"id": row.id, "content_item_id": row.content_item_id, "engine": row.engine,
            "engine_version": row.engine_version, "version": row.version,
            "revision_hash": row.revision_hash, "native_payload": row.native_payload,
            "sidecar": row.sidecar, "manifest": row.manifest}


def submit_review(db: Session, ctx: WorkspaceContext, payload: ReviewSubmission) -> dict:
    item = _require(db, ctx, models.ContentItem, payload.content_item_id)
    document = _require(db, ctx, models.DesignDocument, payload.design_document_id)
    if document.content_item_id != item.id:
        raise WorkspaceError("WORKSPACE_ACCESS_DENIED", "设计文档不属于该内容", 403)
    for approval in db.scalars(select(models.Approval).where(
            models.Approval.workspace_id == ctx.workspace_id,
            models.Approval.content_item_id == item.id,
            models.Approval.status == "approved")).all():
        approval.status = "invalidated"
        approval.invalidated_reason = "new revision submitted"
    revision = models.ContentRevision(workspace_id=ctx.workspace_id, content_item_id=item.id,
                                      design_document_id=document.id,
                                      manifest_hash=document.revision_hash, status="IN_REVIEW")
    db.add(revision)
    item.status = "IN_REVIEW"
    db.flush()
    return {"revision_id": revision.id, "manifest_hash": revision.manifest_hash, "status": revision.status}


def approve(db: Session, ctx: WorkspaceContext, payload: ApprovalIn) -> dict:
    item = _require(db, ctx, models.ContentItem, payload.content_item_id)
    revision = _require(db, ctx, models.ContentRevision, payload.revision_id)
    if revision.content_item_id != item.id or revision.status != "IN_REVIEW":
        raise WorkspaceError("APPROVAL_REQUIRED", "只能审批处于待审状态的当前版本", 409)
    approval = models.Approval(workspace_id=ctx.workspace_id, content_item_id=item.id,
                               revision_id=revision.id, account_ref=payload.account_ref,
                               approved_window=payload.approved_window, approved_by=payload.approved_by,
                               status="approved")
    db.add(approval)
    revision.status = "APPROVED"
    item.status = "APPROVED"
    db.flush()
    audit(db, ctx, "approval.create", "content_revision", revision.id, {"account_ref": payload.account_ref})
    return {"approval_id": approval.id, "revision_id": revision.id, "status": "approved"}


def create_publish_plan(db: Session, ctx: WorkspaceContext, payload: PublishPlanIn) -> dict:
    item = _require(db, ctx, models.ContentItem, payload.content_item_id)
    revision = _require(db, ctx, models.ContentRevision, payload.revision_id)
    approval = db.scalars(select(models.Approval).where(
        models.Approval.workspace_id == ctx.workspace_id,
        models.Approval.content_item_id == item.id,
        models.Approval.revision_id == revision.id,
        models.Approval.status == "approved")).first()
    if not approval:
        raise WorkspaceError("APPROVAL_REQUIRED", "该版本没有有效审批，不能创建发布计划", 409)
    plan = models.PublishPlan(workspace_id=ctx.workspace_id, content_item_id=item.id,
                              revision_id=revision.id, account_ref=payload.account_ref,
                              mode=payload.mode, scheduled_at=payload.scheduled_at,
                              status="READY", payload={"approval_id": approval.id})
    db.add(plan)
    db.flush()
    return {"plan_id": plan.id, "mode": plan.mode, "status": plan.status,
            "note": "预约是本站持久化任务，不代表平台提供预约 API"}


def execute_publish(db: Session, ctx: WorkspaceContext, plan_id: str) -> dict:
    plan = _require(db, ctx, models.PublishPlan, plan_id)
    if plan.mode != "manual":
        raise WorkspaceError("CAPABILITY_DISABLED", "连接器写发布待联调；请使用人工发布路径", 501)
    attempt = models.PublishAttempt(workspace_id=ctx.workspace_id, publish_plan_id=plan.id,
                                    idempotency_key=f"{plan.id}:manual", status="WAITING_MANUAL")
    db.add(attempt)
    plan.status = "WAITING_MANUAL"
    db.flush()
    return {"attempt_id": attempt.id, "status": attempt.status,
            "message": "请到官方界面发布，然后回填笔记链接"}


def manual_result(db: Session, ctx: WorkspaceContext, plan_id: str, payload: ManualResultIn) -> dict:
    plan = _require(db, ctx, models.PublishPlan, plan_id)
    attempt = db.scalars(select(models.PublishAttempt).where(
        models.PublishAttempt.publish_plan_id == plan.id).order_by(
        models.PublishAttempt.created_at.desc())).first()
    if not attempt:
        attempt = models.PublishAttempt(workspace_id=ctx.workspace_id, publish_plan_id=plan.id,
                                        idempotency_key=f"{plan.id}:manual", status="REPORTED_PUBLISHED")
        db.add(attempt)
    attempt.status = "REPORTED_PUBLISHED"
    attempt.result = {"url": payload.url, "platform_note_id": payload.platform_note_id,
                      "reported_at": (payload.reported_at or datetime.now(timezone.utc)).isoformat()}
    note = models.PublishedNote(workspace_id=ctx.workspace_id, account_ref=plan.account_ref,
                                platform_note_id=payload.platform_note_id, url=payload.url,
                                revision_id=plan.revision_id, publish_status="REPORTED_PUBLISHED",
                                visibility_status="unknown")
    db.add(note)
    plan.status = "REPORTED_PUBLISHED"
    db.flush()
    return {"attempt_id": attempt.id, "status": attempt.status,
            "note_id": note.id, "visibility_status": note.visibility_status,
            "message": "已回填，待核对；不代表已公开可见"}


def overview(db: Session, ctx: WorkspaceContext) -> dict:
    return {
        "publish": {"planned": 0, "verified": 0, "overdue": 0, "pending_reconcile": 0},
        "metrics": [],
        "product_interest": {"confirmed": 0, "open_questions": 0},
        "data_coverage": {"creator_metrics": "not_connected", "public_connector": "pending",
                          "manual_observation": "available"},
        "note": "M1 只提供合同与空态；真实数据接入登记为待联调，不以 0 冒充真实值。",
    }


def list_comments(db: Session, ctx: WorkspaceContext) -> dict:
    return {"comments": [], "source": "not_connected",
            "note": "评论样本接入待联调；此处不返回伪造评论。"}


def list_alerts(db: Session, ctx: WorkspaceContext) -> dict:
    return {"alerts": [], "note": "M1 只建表与合同；规则引擎待 M3。"}


def list_publish_plans(db: Session, ctx: WorkspaceContext) -> dict:
    plans = db.scalars(select(models.PublishPlan).where(
        models.PublishPlan.workspace_id == ctx.workspace_id).order_by(
        models.PublishPlan.created_at.desc()).limit(50)).all()
    notes = db.scalars(select(models.PublishedNote).where(
        models.PublishedNote.workspace_id == ctx.workspace_id).order_by(
        models.PublishedNote.reported_at.desc()).limit(50)).all()
    return {
        "plans": [{"id": row.id, "content_item_id": row.content_item_id, "revision_id": row.revision_id,
                   "account_ref": row.account_ref, "mode": row.mode, "status": row.status,
                   "scheduled_at": row.scheduled_at.isoformat() if row.scheduled_at else None,
                   "created_at": row.created_at.isoformat()} for row in plans],
        "notes": [{"id": row.id, "account_ref": row.account_ref, "url": row.url,
                   "platform_note_id": row.platform_note_id, "publish_status": row.publish_status,
                   "visibility_status": row.visibility_status,
                   "reported_at": row.reported_at.isoformat()} for row in notes],
        "note": "SUBMITTED/REPORTED_PUBLISHED 不等于已核对公开可见；visibility_status 独立记录。",
    }


def list_retrospectives(db: Session, ctx: WorkspaceContext) -> dict:
    return {"retrospectives": [], "playbooks": [],
            "note": "复盘与经验库在 M3 接入；M1 只提供合同，不生成推测结论。"}
