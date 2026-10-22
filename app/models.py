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


# ---------------------------------------------------------------- LangGraph runtime ledgers
# NEW tables only. No existing table is altered, so a deployed SQLite file needs no ALTER
# migration: create_all() simply adds these alongside the current tables.

class AgentRequestRecord(Base):
    """Request-scoped idempotency: (conversation_id, key) + normalized payload hash.

    Replaces the earlier global-only AgentRun.idempotency_key lookup, which allowed the
    same key to collide across conversations and never checked the payload.
    """
    __tablename__ = "agent_request_records"
    __table_args__ = (UniqueConstraint("conversation_id", "request_key", name="uq_request_scope_key"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    conversation_id: Mapped[str] = mapped_column(String(32), index=True)
    request_key: Mapped[str] = mapped_column(String(120))
    payload_hash: Mapped[str] = mapped_column(String(64), default="")
    run_id: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AgentModelAttempt(Base):
    """One real model request. Missing usage is recorded as unknown, never as zero."""
    __tablename__ = "agent_model_attempts"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    conversation_id: Mapped[str] = mapped_column(String(32), index=True)
    run_id: Mapped[str] = mapped_column(String(32), index=True)
    decision_index: Mapped[int] = mapped_column(Integer, default=0)
    kind: Mapped[str] = mapped_column(String(40), default="decide")
    model_id: Mapped[str] = mapped_column(String(80), default="")
    protocol_mode: Mapped[str] = mapped_column(String(20), default="native_tools")
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    usage_certainty: Mapped[str] = mapped_column(String(20), default="unknown")
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AgentSeqCounter(Base):
    """Per-conversation message sequence allocator.

    ``max(seq) + 1`` in Python loses updates when two writers interleave, which is how
    the legacy batch formula also produced duplicate sequence numbers. The counter is
    incremented in SQL inside the writer's own transaction.
    """
    __tablename__ = "agent_seq_counters"
    conversation_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    next_seq: Mapped[int] = mapped_column(Integer, default=0)


# --------------------------------------------------------------------------- hotspots
# The hotspot centre of the plan, implemented inside this application: the contract, the
# data model and the honesty rules are the plan's, while the storage is SQLite rather than
# PostgreSQL. That difference is real and is stated on every response
# (``production_ready: false``); SQLite is a single-writer pilot store and the plan's
# PostgreSQL-only constraints - advisory locks, SKIP LOCKED claiming, atomic release
# publication under concurrency - are NOT exercised here.


class CostReservation(Base):
    """A budget reservation, taken *before* a paid attempt is submitted.

    The plan is specific about the accounting: reserve inside the transaction before submit,
    then settle to the real amount or release it; every paid attempt of one request shares
    that request's budget; and an unknown cost becomes a reconciliation item rather than being
    zeroed. Zeroing an unknown charge is how a system quietly spends money it cannot account
    for, so ``state='unknown'`` is a first-class outcome here.
    """
    __tablename__ = "cost_reservations"
    __table_args__ = (UniqueConstraint("tenant_id", "idempotency_key",
                                       name="uq_cost_idempotency"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True, default="default")
    request_id: Mapped[str] = mapped_column(String(64), index=True, default="")
    conversation_id: Mapped[str] = mapped_column(String(32), index=True, default="")
    idempotency_key: Mapped[str] = mapped_column(String(128), index=True, default="")
    purpose: Mapped[str] = mapped_column(String(40), default="image_generate")
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    state: Mapped[str] = mapped_column(String(20), index=True, default="reserved")
    currency: Mapped[str] = mapped_column(String(8), default="")
    estimated: Mapped[float] = mapped_column(Float, nullable=True, default=None)
    actual: Mapped[float] = mapped_column(Float, nullable=True, default=None)
    provider: Mapped[str] = mapped_column(String(60), default="")
    note: Mapped[str] = mapped_column(String(300), default="")
    needs_reconciliation: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                        nullable=True, default=None)


class HotspotSource(Base):
    """A candidate public source. Disabled and pending until someone approves it.

    The plan's templates ship every source ``enabled=false`` with
    ``approval_status="pending"`` as a deliberate safety design, not as a default to flip.
    Enabling one requires recorded evidence, so the columns for it are here and empty.
    """
    __tablename__ = "hs_sources"
    source_id: Mapped[str] = mapped_column(String(60), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(120), default="")
    origin_platform: Mapped[str] = mapped_column(String(60), default="")
    provider: Mapped[str] = mapped_column(String(60), default="newsnow")
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    approval_status: Mapped[str] = mapped_column(String(20), default="pending")
    approval_evidence: Mapped[str] = mapped_column(String(400), default="")
    approved_by: Mapped[str] = mapped_column(String(120), default="")
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                         nullable=True, default=None)
    min_interval_seconds: Mapped[int] = mapped_column(Integer, default=600)
    locked_commit: Mapped[str] = mapped_column(String(120), default="")
    container_digest: Mapped[str] = mapped_column(String(160), default="")
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                             nullable=True, default=None)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                            nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class HotspotRefreshGroup(Base):
    """One refresh request. Tasks are persistent rows, not in-process futures."""
    __tablename__ = "hs_refresh_groups"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True, default="default")
    user_id: Mapped[str] = mapped_column(String(64), index=True, default="")
    state: Mapped[str] = mapped_column(String(24), index=True, default="queued")
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                          nullable=True, default=None)
    idempotency_key: Mapped[str] = mapped_column(String(128), index=True, default="")
    reused_from: Mapped[str] = mapped_column(String(32), default="")


class HotspotSourceCheck(Base):
    """One source fetch attempt, with the six times kept apart.

    ``verified_upstream_at`` is set **only** when the fetch actually reached the origin.
    A cache hit does not advance it: "HTTP 200 only proves the response succeeded". Keeping
    this separate from ``checked_at`` is the whole point - our previous implementation wrote
    the local request time as if it were the platform's update time.
    """
    __tablename__ = "hs_source_checks"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    group_id: Mapped[str] = mapped_column(String(32), index=True, default="")
    source_id: Mapped[str] = mapped_column(String(60), index=True)
    state: Mapped[str] = mapped_column(String(20), default="queued")
    outcome: Mapped[str] = mapped_column(String(24), default="")
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                        nullable=True, default=None)
    verified_upstream_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                                  nullable=True, default=None)
    origin_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                               nullable=True, default=None)
    snapshot_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                                 nullable=True, default=None)
    http_status: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    data_mode: Mapped[str] = mapped_column(String(24), default="")
    entries_count: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(String(300), default="")


class HotspotSnapshot(Base):
    """A frozen capture of one source's leaderboard."""
    __tablename__ = "hs_snapshots"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    source_id: Mapped[str] = mapped_column(String(60), index=True)
    check_id: Mapped[str] = mapped_column(String(32), index=True, default="")
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    origin_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                               nullable=True, default=None)
    entries: Mapped[list] = mapped_column(JSON, default=list)
    content_hash: Mapped[str] = mapped_column(String(64), default="")


class HotspotItem(Base):
    """A single leaderboard entry as observed from one source.

    Uniqueness is ``(source_id, external_key)`` and deliberately **not**
    ``(source_id, content_hash)``: as the plan puts it, "A->B->A is a new observation".
    """
    __tablename__ = "hs_items"
    __table_args__ = (UniqueConstraint("source_id", "external_key", name="uq_hs_item_key"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    source_id: Mapped[str] = mapped_column(String(60), index=True)
    external_key: Mapped[str] = mapped_column(String(200))
    title: Mapped[str] = mapped_column(String(300), default="")
    url: Mapped[str] = mapped_column(String(600), default="")
    rank: Mapped[int] = mapped_column(Integer, default=0)
    domain: Mapped[str] = mapped_column(String(30), index=True, default="其他")
    features: Mapped[dict] = mapped_column(JSON, default=dict)
    risk_flags: Mapped[list] = mapped_column(JSON, default=list)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    origin_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                               nullable=True, default=None)


class HotspotEvent(Base):
    """Items from different sources that describe the same happening, clustered conservatively."""
    __tablename__ = "hs_events"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    canonical_title: Mapped[str] = mapped_column(String(300), default="")
    domain: Mapped[str] = mapped_column(String(30), index=True, default="其他")
    features: Mapped[dict] = mapped_column(JSON, default=dict)
    risk_flags: Mapped[list] = mapped_column(JSON, default=list)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class HotspotEventMember(Base):
    __tablename__ = "hs_event_members"
    __table_args__ = (UniqueConstraint("event_id", "item_id", name="uq_hs_member"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    event_id: Mapped[str] = mapped_column(String(32), index=True)
    item_id: Mapped[str] = mapped_column(String(32), index=True)
    source_id: Mapped[str] = mapped_column(String(60), index=True, default="")
    origin_platform: Mapped[str] = mapped_column(String(60), default="")
    provider: Mapped[str] = mapped_column(String(60), default="")


class HotspotBoardRelease(Base):
    """A published board. Readers never see a half-built one."""
    __tablename__ = "hs_board_releases"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    rule_version: Mapped[str] = mapped_column(String(60), default="")
    taxonomy_version: Mapped[str] = mapped_column(String(60), default="")
    data_mode: Mapped[str] = mapped_column(String(30), default="no_approved_sources")
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    published: Mapped[bool] = mapped_column(Boolean, default=False)
    candidate_pool: Mapped[list] = mapped_column(JSON, default=list)
    source_statuses: Mapped[list] = mapped_column(JSON, default=list)
    production_ready: Mapped[bool] = mapped_column(Boolean, default=False)
    notice: Mapped[str] = mapped_column(String(400), default="")


class HotspotBoardEntry(Base):
    __tablename__ = "hs_board_entries"
    __table_args__ = (UniqueConstraint("release_id", "view_key", "rank", name="uq_hs_entry_rank"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    release_id: Mapped[str] = mapped_column(String(32), index=True)
    view_key: Mapped[str] = mapped_column(String(40), index=True)
    rank: Mapped[int] = mapped_column(Integer, default=0)
    event_id: Mapped[str] = mapped_column(String(32), index=True)
    general_score: Mapped[float] = mapped_column(Float, default=0.0)
    scenario_score: Mapped[float] = mapped_column(Float, default=0.0)
    reason: Mapped[dict] = mapped_column(JSON, default=dict)


class HotspotFavorite(Base):
    __tablename__ = "hs_favorites"
    __table_args__ = (UniqueConstraint("tenant_id", "user_id", "event_id", name="uq_hs_fav"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True, default="default")
    user_id: Mapped[str] = mapped_column(String(64), index=True, default="")
    event_id: Mapped[str] = mapped_column(String(32), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class HotspotSelection(Base):
    """An immutable hand-off of chosen material to the creation agent.

    Immutable on purpose: the plan requires that what the agent was given cannot change
    afterwards. Saving a selection does **not** trigger generation.
    """
    __tablename__ = "hs_selections"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True, default="default")
    user_id: Mapped[str] = mapped_column(String(64), index=True, default="")
    release_id: Mapped[str] = mapped_column(String(32), index=True, default="")
    event_ids: Mapped[list] = mapped_column(JSON, default=list)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class HotspotIdempotency(Base):
    """Write requests repeat safely: same key + same request reuses, different request 409s."""
    __tablename__ = "hs_idempotency"
    __table_args__ = (UniqueConstraint("tenant_id", "user_id", "key", name="uq_hs_idem"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True, default="default")
    user_id: Mapped[str] = mapped_column(String(64), index=True, default="")
    key: Mapped[str] = mapped_column(String(128))
    request_digest: Mapped[str] = mapped_column(String(64), default="")
    response: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AgentOutboxEvent(Base):
    """A reliable handoff record, written in the *same* transaction as the content.

    The plan requires this: registering content and recording the handoff event must commit
    together, or a delivery can exist with no record of what should happen next. Delivery to
    a consumer happens separately with bounded retries, so a consumer being down delays the
    event instead of losing it.

    state: pending | delivered | failed
    """
    __tablename__ = "agent_outbox_events"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    conversation_id: Mapped[str] = mapped_column(String(32), index=True)
    run_id: Mapped[str] = mapped_column(String(32), index=True)
    workspace_id: Mapped[str] = mapped_column(String(40), index=True, default="default")
    event_type: Mapped[str] = mapped_column(String(60), index=True, default="")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    state: Mapped[str] = mapped_column(String(20), index=True, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                          nullable=True, default=None)


class AgentContentEmbedding(Base):
    """Vector memory for the semantic layer, one row per run and view.

    ``index_version`` identifies the vector space (model id + dimension). Vectors from two
    versions are never compared, because a distance across two embedding models is
    meaningless. Same authorisation and retention scope as the text they describe.
    """
    __tablename__ = "agent_content_embeddings"
    __table_args__ = (UniqueConstraint("run_id", "view", name="uq_embedding_run_view"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    conversation_id: Mapped[str] = mapped_column(String(32), index=True)
    run_id: Mapped[str] = mapped_column(String(32), index=True)
    workspace_id: Mapped[str] = mapped_column(String(40), index=True, default="default")
    view: Mapped[str] = mapped_column(String(30), default="full")
    model_id: Mapped[str] = mapped_column(String(120), default="")
    dimension: Mapped[int] = mapped_column(Integer, default=0)
    index_version: Mapped[str] = mapped_column(String(160), index=True, default="")
    vector: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AgentRunLease(Base):
    """A persisted lease over a run, so a worker crash is detectable and recoverable.

    The in-process conversation lock is only a local lease: it says nothing to a second
    worker and nothing after a restart. This row is the durable claim, with a heartbeat and
    an expiry, plus a bounded attempt counter so a run cannot be retried forever.

    state: held | released
    """
    __tablename__ = "agent_run_leases"
    __table_args__ = (UniqueConstraint("run_id", name="uq_lease_run"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(String(32), index=True)
    conversation_id: Mapped[str] = mapped_column(String(32), index=True)
    owner: Mapped[str] = mapped_column(String(120), default="")
    state: Mapped[str] = mapped_column(String(20), default="held")
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                        nullable=True, default=None)
    reason: Mapped[str] = mapped_column(String(120), default="")


class AgentContentFingerprint(Base):
    """Shadow-mode content memory: evidence for near-duplicate detection.

    Registered when content is *delivered*, not only when published, and scoped to a single
    workspace. Cross-store scope authorisation is NOT implemented, so rows here must never
    be described as brand-wide deduplication.
    """
    __tablename__ = "agent_content_fingerprints"
    __table_args__ = (UniqueConstraint("run_id", name="uq_fingerprint_run"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    conversation_id: Mapped[str] = mapped_column(String(32), index=True)
    run_id: Mapped[str] = mapped_column(String(32), index=True)
    workspace_id: Mapped[str] = mapped_column(String(40), index=True, default="default")
    artifact_id: Mapped[str] = mapped_column(String(32), default="")
    algorithm: Mapped[str] = mapped_column(String(40), default="")
    normalized_sha256: Mapped[str] = mapped_column(String(64), index=True, default="")
    length: Mapped[int] = mapped_column(Integer, default=0)
    shingles: Mapped[int] = mapped_column(Integer, default=0)
    minhash: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AgentEffect(Base):
    """Side-effect ledger so a replayed tool call does not duplicate a business write.

    state: pending | completed | sent_outcome_unknown | failed
    """
    __tablename__ = "agent_effects"
    __table_args__ = (UniqueConstraint("call_uid", name="uq_effect_call_uid"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    conversation_id: Mapped[str] = mapped_column(String(32), index=True)
    run_id: Mapped[str] = mapped_column(String(32), index=True)
    call_uid: Mapped[str] = mapped_column(String(120))
    tool_name: Mapped[str] = mapped_column(String(60), default="")
    payload_hash: Mapped[str] = mapped_column(String(64), default="")
    state: Mapped[str] = mapped_column(String(30), default="pending")
    outcome: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now,
                                                 onupdate=_now)
