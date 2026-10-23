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
             "按该交付面的规格写作：",
             # 2026-10-22 裁定：用户明确要求的字数优先于规格。规格只是编辑建议，不是平台规则，
             # 所以不能拿它去覆盖用户说过的要求。
             "- 用户在对话里明确要求的字数优先于下面的建议区间；两者冲突时按用户要求。"]
    if row.get("body_soft_range"):
        lines.append("- 正文建议 " + "-".join(str(v) for v in row["body_soft_range"])
                     + " 字（编辑建议，不是平台规则）")
    if row.get("title_candidates"):
        lines.append("- 标题候选 " + str(row["title_candidates"]) + " 个，单个建议不超过 "
                     + str(row.get("title_soft_max") or "") + " 字")
    if row.get("card_count_range"):
        # 7 页图文已删除（规格 §0）；交付物固定 3 篇文案，不能再按卡片页数写。
        lines.append("- 交付物固定为 3 篇文案（标题+正文+话题标签），不要产出卡片页数或长图")
    if row.get("headline_soft_max"):
        lines.append("- 主标题建议不超过 " + str(row["headline_soft_max"]) + " 字")
    if row.get("max_text_lines"):
        lines.append("- 文字不超过 " + str(row["max_text_lines"]) + " 行")
    if row.get("required_fields"):
        lines.append("- 必须包含字段：" + "、".join(row["required_fields"]))
    if row.get("compliance_note"):
        lines.append("- 合规：" + str(row["compliance_note"]))
    if row.get("spec_gap"):
        lines.append("- 规格说明：" + str(row["spec_gap"]))
    if info.get("unconfirmed"):
        lines.append("- 注意：" + str(info.get("open_question") or "该交付面含义未确认，不得按其他平台规格替代"))
    lines.append("交付面与锁定由用户选择；不要自行更换，也不要按其他交付面的长度写作。")
    return "\n".join(lines)


def material_directive(db: Session, conversation: models.AgentConversation) -> str:
    """The accumulative task archive, shown to the model on every decide step.

    Read-only presentation: the model is told to write through ``update_task_material`` so the
    archive has a single writer, and so a later turn sees exactly what earlier turns recorded.
    """
    from .. import task_material

    state = db.get(models.AgentTaskState, conversation.id)
    data = dict(state.data or {}) if state else {}
    material = task_material.normalize(data)
    lines = ["【本次任务资料（可累加；要补充请调用 update_task_material，不要直接编）】"]
    labels = (("audience", "目标人群"), ("platform", "发布平台"),
              ("scenario", "使用环境·场景"), ("campaign", "要打的活动"))
    lines.append("- 目标人群：" + (material["audience"] or "（未收集）"))
    lines.append("- 发布平台：" + (material["platform"] or "（未收集）"))
    lines.append("- 使用环境·场景：" + (material["scenario"] or "（未收集）"))
    lines.append("- 要打的活动：" + (material["campaign"] or "（未收集，可回“没有”）"))
    model_rows = material.get("models") or []
    models_text = "、".join(str(row.get("model") or row.get("product_id") or "")
                            for row in model_rows if row.get("model") or row.get("product_id"))
    lines.append("- 选定型号：" + (models_text or
                                   "（未确定：先收齐四件事，再去知识库匹配 2–4 款候选让用户选 1–2 款）"))
    hotspot = material.get("hotspot") or {}
    if str(hotspot.get("title") or "").strip():
        source = str(hotspot.get("source") or "").strip()
        lines.append("- 参考热点：" + str(hotspot["title"]) + (("（来源：" + source + "）") if source else ""))
    else:
        lines.append("- 参考热点：（无）")
    missing = [label for key, label in labels if not material.get(key)]
    if not model_rows:
        missing.append("选定型号")
    lines.append("缺少：" + ("、".join(missing) if missing else
                            "无——资料已齐：先只回一句很短的确认（是否输出最后交付内容？）"))
    lines.append("规则：不要问型号；用户已点名型号时直接查证据；交付固定 3 篇混着写的文案，"
                 "不产出页（pages）结构。")
    return "\n".join(lines)


def build_context(db: Session, conversation: models.AgentConversation, run: models.AgentRun,
                  model: str | None = None) -> RuntimeContext:
    def cancelled() -> bool:
        # A persisted cancel intent, read on its own short transaction: a cancel that
        # arrives while a model call is in flight must stop the *next* step. Reading it
        # through this session's identity map would silently miss it.
        return ledger.fresh_run_status(run.id) == "cancelled"

    tool_ctx = agent_runtime.ToolContext(db=db, conversation=conversation, run=run)
    directive = "\n\n".join(part for part in (material_directive(db, conversation),
                                              surface_directive(db, conversation)) if part)
    return RuntimeContext(
        db=db, conversation=conversation, run=run, model=model,
        gateway=ModelGateway(db, conversation_id=conversation.id, run_id=run.id,
                             model=model, cancel_check=cancelled,
                             directive=directive),
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