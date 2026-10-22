"""Trusted runtime context.

Deliberately kept *out* of the checkpointed state: the SQLAlchemy session, the model
client, the knowledge-base client and any credential must never be serialized into a
checkpoint. Only ids and hashes cross that boundary (see :mod:`.state`).
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Callable

from sqlalchemy.orm import Session

from .. import agent_runtime, models
from . import ledger
from .model_gateway import ModelGateway
from .tool_gateway import ToolGateway


def _env_int(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return min(high, max(low, value))


@dataclass
class RuntimeContext:
    db: Session
    conversation: models.AgentConversation
    run: models.AgentRun
    model: str | None
    gateway: ModelGateway
    tools: ToolGateway
    tool_ctx: agent_runtime.ToolContext
    max_decisions: int
    cancel_check: Callable[[], bool]
    budget_seconds: int = 180
    model_call_timeout: int = 140
    # The model-call cap alone left tool fan-out unbounded. The plan's policy config puts
    # max_tool_requests at 12; exceeding it stops the run honestly rather than quietly
    # spending more.
    max_tool_calls: int = 12
    started_at: float = 0.0

    @property
    def conversation_id(self) -> str:
        return self.conversation.id

    @property
    def run_id(self) -> str:
        return self.run.id

    @property
    def thread_id(self) -> str:
        from . import RUNNER_VERSION
        return f"{self.conversation.id}:{RUNNER_VERSION}"

    @property
    def deadline(self) -> float:
        return self.started_at + self.budget_seconds

    def out_of_time(self) -> bool:
        return time.monotonic() > self.deadline


def surface_directive(db: Session, conversation: models.AgentConversation) -> str:
    """The instruction for a conversation's locked delivery surface, or an empty string.

    The lock is stored on the task state, so it survives across runs and a user does not have to
    re-select the surface every turn. It is written as a directive because that is what it is:
    the surface changes the required length, structure and fields, so the model has to know it.
    """
    state = db.get(models.AgentTaskState, conversation.id)
    data = dict(state.data or {}) if state else {}
    surface_id = str(data.get("surface") or "").strip()
    if not surface_id:
        return ""
    from .. import surfaces
    info = surfaces.resolve(surface_id)
    if info.get("error") or not info.get("surface"):
        return ""
    row = info["spec"] or {}
    lines = ["【本次交付面已锁定：" + str(row.get("label") or surface_id) + "】",
             "按该交付面的规格写作："]
    if row.get("body_soft_range"):
        lines.append("- 正文建议 " + "-".join(str(v) for v in row["body_soft_range"])
                     + " 字（编辑建议，不是平台规则）")
    if row.get("title_candidates"):
        lines.append("- 标题候选 " + str(row["title_candidates"]) + " 个，单个建议不超过 "
                     + str(row.get("title_soft_max") or "") + " 字")
    if row.get("card_count_range"):
        lines.append("- 卡片 " + "-".join(str(v) for v in row["card_count_range"]) + " 页")
    if row.get("headline_soft_max"):
        lines.append("- 主标题建议不超过 " + str(row["headline_soft_max"]) + " 字")
    if row.get("required_fields"):
        lines.append("- 必须包含字段：" + "、".join(row["required_fields"]))
    if info.get("unconfirmed"):
        lines.append("- 注意：" + str(info.get("open_question") or "该交付面含义未确认，不得按其他平台规格替代"))
    lines.append("交付面与锁定由用户选择；不要自行更换，也不要按其他交付面的长度写作。")
    return "\n".join(lines)


def build_context(db: Session, conversation: models.AgentConversation, run: models.AgentRun,
                  model: str | None = None) -> RuntimeContext:
    def cancelled() -> bool:
        # A persisted cancel intent, read on its own short transaction: a cancel that
        # arrives while a model call is in flight must stop the *next* step. Reading it
        # through this session's identity map would silently miss it.
        return ledger.fresh_run_status(run.id) == "cancelled"

    tool_ctx = agent_runtime.ToolContext(db=db, conversation=conversation, run=run)
    return RuntimeContext(
        db=db, conversation=conversation, run=run, model=model,
        gateway=ModelGateway(db, conversation_id=conversation.id, run_id=run.id,
                             model=model, cancel_check=cancelled,
                             directive=surface_directive(db, conversation)),
        tools=ToolGateway(tool_ctx, cancel_check=cancelled),
        tool_ctx=tool_ctx,
        max_decisions=_env_int("LG_MAX_MODEL_CALLS", 12, 1, 40),
        cancel_check=cancelled,
        budget_seconds=_env_int("AGENT_RUN_BUDGET_SECONDS", 180, 30, 600),
        max_tool_calls=_env_int("LG_MAX_TOOL_CALLS", 12, 1, 60),
        # A second net above the transport-level deadline in app/model.py: even if a
        # blocking call cannot be interrupted on this platform, the graph still moves on.
        # LG_MODEL_CALL_TIMEOUT exists so tests can use a small value.
        model_call_timeout=_env_int("LG_MODEL_CALL_TIMEOUT",
                                    _env_int("LLM_TIMEOUT", 120, 10, 170) + 20, 1, 600),
        started_at=time.monotonic(),
    )