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
                             model=model, cancel_check=cancelled),
        tools=ToolGateway(tool_ctx, cancel_check=cancelled),
        tool_ctx=tool_ctx,
        max_decisions=_env_int("LG_MAX_MODEL_CALLS", 12, 1, 40),
        cancel_check=cancelled,
        budget_seconds=_env_int("AGENT_RUN_BUDGET_SECONDS", 180, 30, 600),
        started_at=time.monotonic(),
    )