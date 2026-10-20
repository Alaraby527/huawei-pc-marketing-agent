"""SQLAlchemy models for the Rednote workspace module (M1 subset).

Every business row carries workspace_id; services must scope queries by it.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text,
                        UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Workspace(Base):
    __tablename__ = "workspaces"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default="default")
    name: Mapped[str] = mapped_column(String(200), default="默认工作区")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class WorkspaceAppSetting(Base):
    __tablename__ = "workspace_app_settings"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(String(64), ForeignKey("workspaces.id"), index=True)
    app: Mapped[str] = mapped_column(String(40), default="rednote")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class BriefHandoff(Base):
    __tablename__ = "brief_handoffs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    source_conversation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="consumed")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class BriefSnapshot(Base):
    __tablename__ = "brief_snapshots"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    handoff_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    schema_version: Mapped[str] = mapped_column(String(20), default="1.0")
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Product(Base):
    __tablename__ = "products"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    model_name: Mapped[str] = mapped_column(String(200), default="")
    configuration: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    claims: Mapped[list["ProductClaim"]] = relationship(back_populates="product", cascade="all, delete-orphan")


class ProductClaim(Base):
    __tablename__ = "product_claims"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    product_id: Mapped[str] = mapped_column(String(32), ForeignKey("products.id"), index=True)
    claim_id: Mapped[str] = mapped_column(String(80), default="")
    text: Mapped[str] = mapped_column(Text, default="")
    evidence_id: Mapped[str] = mapped_column(String(80), default="")
    verification_status: Mapped[str] = mapped_column(String(30), default="verified")
    valid_until: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    product: Mapped[Product] = relationship(back_populates="claims")


class Asset(Base):
    __tablename__ = "assets"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(30), default="image")
    filename: Mapped[str] = mapped_column(String(300), default="")
    mime: Mapped[str] = mapped_column(String(80), default="image/png")
    sha256: Mapped[str] = mapped_column(String(64), default="")
    path: Mapped[str] = mapped_column(String(500), default="")
    source: Mapped[str] = mapped_column(String(200), default="")
    license_note: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ContentItem(Base):
    __tablename__ = "content_items"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    brief_snapshot_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    product_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    account_ref: Mapped[str] = mapped_column(String(120), default="")
    objective: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(40), default="BRIEF")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class ResearchRun(Base):
    __tablename__ = "research_runs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    content_item_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    query: Mapped[str] = mapped_column(String(300), default="")
    source_mode: Mapped[str] = mapped_column(String(40), default="public")
    status: Mapped[str] = mapped_column(String(30), default="succeeded")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class SourceRecord(Base):
    __tablename__ = "source_records"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    research_run_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    title: Mapped[str] = mapped_column(String(300), default="")
    url: Mapped[str] = mapped_column(String(1000), default="")
    source: Mapped[str] = mapped_column(String(80), default="")
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)


class TrendSelection(Base):
    __tablename__ = "trend_selections"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    content_item_id: Mapped[str] = mapped_column(String(32), index=True)
    main_topic: Mapped[dict] = mapped_column(JSON, default=dict)
    aux_topics: Mapped[list] = mapped_column(JSON, default=list)
    scenario: Mapped[str] = mapped_column(String(300), default="")
    benefits: Mapped[list] = mapped_column(JSON, default=list)
    evidence_ids: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class CreativeDirection(Base):
    __tablename__ = "creative_directions"
    __table_args__ = (UniqueConstraint("content_item_id", "ordinal", name="uq_direction_ordinal"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    content_item_id: Mapped[str] = mapped_column(String(32), index=True)
    ordinal: Mapped[int] = mapped_column(Integer, default=1)
    expression_id: Mapped[str] = mapped_column(String(10), default="C01")
    family_id: Mapped[str] = mapped_column(String(10), default="V01")
    narrative_id: Mapped[str] = mapped_column(String(10), default="N01")
    product_mode_id: Mapped[str] = mapped_column(String(10), default="P02")
    title: Mapped[str] = mapped_column(String(200), default="")
    hook: Mapped[str] = mapped_column(Text, default="")
    why_product: Mapped[str] = mapped_column(Text, default="")
    risk: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class CreativeBoard(Base):
    __tablename__ = "creative_boards"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    content_item_id: Mapped[str] = mapped_column(String(32), index=True)
    direction_ids: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(30), default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class PreviewCandidate(Base):
    __tablename__ = "preview_candidates"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    board_id: Mapped[str] = mapped_column(String(32), index=True)
    direction_id: Mapped[str] = mapped_column(String(32), index=True)
    role: Mapped[str] = mapped_column(String(20), default="cover")
    asset_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class DesignDocument(Base):
    __tablename__ = "design_documents"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    content_item_id: Mapped[str] = mapped_column(String(32), index=True)
    engine: Mapped[str] = mapped_column(String(40), default="vue-fabric-editor")
    engine_version: Mapped[str] = mapped_column(String(40), default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    revision_hash: Mapped[str] = mapped_column(String(64), default="")
    native_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    sidecar: Mapped[dict] = mapped_column(JSON, default=dict)
    manifest: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class ContentRevision(Base):
    __tablename__ = "content_revisions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    content_item_id: Mapped[str] = mapped_column(String(32), index=True)
    design_document_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    manifest_hash: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(30), default="DRAFT")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Approval(Base):
    __tablename__ = "approvals"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    content_item_id: Mapped[str] = mapped_column(String(32), index=True)
    revision_id: Mapped[str] = mapped_column(String(32), index=True)
    account_ref: Mapped[str] = mapped_column(String(120), default="")
    approved_window: Mapped[str] = mapped_column(String(80), default="")
    approved_by: Mapped[str] = mapped_column(String(120), default="")
    status: Mapped[str] = mapped_column(String(30), default="approved")
    invalidated_reason: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class PublishPlan(Base):
    __tablename__ = "publish_plans"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    content_item_id: Mapped[str] = mapped_column(String(32), index=True)
    revision_id: Mapped[str] = mapped_column(String(32), default="")
    account_ref: Mapped[str] = mapped_column(String(120), default="")
    mode: Mapped[str] = mapped_column(String(20), default="manual")
    status: Mapped[str] = mapped_column(String(40), default="READY")
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class PublishAttempt(Base):
    __tablename__ = "publish_attempts"
    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_publish_attempt_key"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    publish_plan_id: Mapped[str] = mapped_column(String(32), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(120), default="")
    status: Mapped[str] = mapped_column(String(40), default="WAITING_MANUAL")
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class PublishedNote(Base):
    __tablename__ = "published_notes"
    __table_args__ = (UniqueConstraint("account_ref", "platform_note_id", name="uq_note_account_platform"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    account_ref: Mapped[str] = mapped_column(String(120), default="")
    platform_note_id: Mapped[str] = mapped_column(String(120), default="")
    url: Mapped[str] = mapped_column(String(1000), default="")
    revision_id: Mapped[str] = mapped_column(String(32), default="")
    publish_status: Mapped[str] = mapped_column(String(40), default="REPORTED_PUBLISHED")
    visibility_status: Mapped[str] = mapped_column(String(40), default="unknown")
    reported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_job_key"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(60), default="")
    status: Mapped[str] = mapped_column(String(30), default="queued")
    idempotency_key: Mapped[str] = mapped_column(String(120), default="")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    actor: Mapped[str] = mapped_column(String(120), default="local-operator")
    action: Mapped[str] = mapped_column(String(80), default="")
    object_type: Mapped[str] = mapped_column(String(60), default="")
    object_id: Mapped[str] = mapped_column(String(64), default="")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ActionTask(Base):
    __tablename__ = "action_tasks"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(60), default="todo")
    title: Mapped[str] = mapped_column(String(300), default="")
    status: Mapped[str] = mapped_column(String(30), default="open")
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class MetricDefinition(Base):
    __tablename__ = "metric_definitions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    key: Mapped[str] = mapped_column(String(80), default="")
    definition: Mapped[str] = mapped_column(Text, default="")
    unit: Mapped[str] = mapped_column(String(40), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class MetricValue(Base):
    __tablename__ = "metric_values"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    note_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    definition_key: Mapped[str] = mapped_column(String(80), default="")
    source: Mapped[str] = mapped_column(String(40), default="manual_observation")
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    raw_value: Mapped[str] = mapped_column(String(80), default="")
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    effective_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    precision_note: Mapped[str] = mapped_column(String(200), default="")


class AgentConversation(Base):
    __tablename__ = "agent_conversations"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(String(64), index=True, default="default")
    runner_version: Mapped[str] = mapped_column(String(40), default="v2")
    revision: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AgentTaskState(Base):
    """Guided-flow hints: needs, candidates, selected product and plan.

    This is deliberately a soft state, not a rigid funnel: later turns may skip,
    repeat or revise any step when the conversation calls for it.
    """
    __tablename__ = "agent_task_states"
    conversation_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    stage: Mapped[str] = mapped_column(String(30), default="intake")
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now,
                                                 onupdate=_now)


class AgentMessage(Base):
    __tablename__ = "agent_messages"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    conversation_id: Mapped[str] = mapped_column(String(32), index=True)
    role: Mapped[str] = mapped_column(String(20), default="user")
    content: Mapped[str] = mapped_column(Text, default="")
    tool_call_id: Mapped[str] = mapped_column(String(80), default="")
    seq: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AgentRun(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_agent_run_key"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    conversation_id: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(30), default="running")
    model: Mapped[str] = mapped_column(String(80), default="")
    idempotency_key: Mapped[str | None] = mapped_column(String(120), nullable=True, default=None)
    events: Mapped[list] = mapped_column(JSON, default=list)
    usage: Mapped[dict] = mapped_column(JSON, default=dict)
    stop_reason: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentArtifact(Base):
    __tablename__ = "agent_artifacts"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    conversation_id: Mapped[str] = mapped_column(String(32), index=True)
    artifact_type: Mapped[str] = mapped_column(String(40), default="post")
    latest_revision: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AgentArtifactRevision(Base):
    __tablename__ = "agent_artifact_revisions"
    __table_args__ = (UniqueConstraint("artifact_id", "revision", name="uq_artifact_revision"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    artifact_id: Mapped[str] = mapped_column(String(32), index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    content: Mapped[dict] = mapped_column(JSON, default=dict)
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    check_status: Mapped[dict] = mapped_column(JSON, default=dict)
    note: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AgentToolExecution(Base):
    __tablename__ = "agent_tool_executions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(String(32), index=True)
    tool_name: Mapped[str] = mapped_column(String(60), default="")
    args_summary: Mapped[dict] = mapped_column(JSON, default=dict)
    result_summary: Mapped[dict] = mapped_column(JSON, default=dict)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str] = mapped_column(String(300), default="")
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
