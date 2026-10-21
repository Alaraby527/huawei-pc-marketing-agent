"""Tool gateway: the only path from the graph to business capabilities.

Business logic is *reused*, not re-implemented: every tool call is delegated to
``agent_runtime.execute_tool`` with the existing :class:`ToolContext`, so grounding,
artifact revisioning, evidence loading and image verification behave exactly as they
already do and stay under the 180-test legacy contract.

What the gateway adds on top:

* an allowlist check (the model cannot invent a tool),
* a cancellation check before any side effect,
* an effect ledger so a replayed ``save_artifact`` after a crash cannot create a
  duplicate artifact revision.
"""
from __future__ import annotations


import json
from typing import Any

from .. import agent_runtime
from . import ledger
from .errors import sanitize_observation

# Tools that mutate business state. Read-only tools are safe to retry freely.
WRITE_TOOLS = frozenset({"save_artifact", "render_visuals"})


class ToolGateway:
    def __init__(self, ctx: agent_runtime.ToolContext, *, cancel_check=None):
        self.ctx = ctx
        self.cancel_check = cancel_check

    @property
    def allowed(self) -> set[str]:
        return set(agent_runtime.TOOL_NAMES)

    async def run(self, name: str, args: dict[str, Any],
                  call_uid: str = "") -> dict[str, Any]:
        if name not in self.allowed:
            return {"ok": False, "error": "TOOL_NOT_ALLOWED", "message": "工具不在白名单",
                    "tool": name}
        if self.cancel_check and self.cancel_check():
            return {"ok": False, "error": "CANCELLED", "message": "任务已取消，未执行该工具"}

        if name in WRITE_TOOLS and call_uid:
            request_hash = ledger.payload_hash({"tool": name, "args": args})
            try:
                previous = ledger.effect_lookup(self.ctx.db, call_uid, request_hash)
            except ledger.RequestConflict as conflict:
                return {"ok": False, "error": "EFFECT_CONFLICT", "message": str(conflict)}
            if previous is not None:
                # Replayed after a crash: return the stored outcome, do not re-write.
                return {**previous, "replayed": True}
            ledger.effect_begin(self.ctx.db, conversation_id=self.ctx.conversation.id,
                                run_id=self.ctx.run.id, call_uid=call_uid,
                                tool_name=name, request_hash=request_hash)

        try:
            outcome = await agent_runtime.execute_tool(self.ctx, name, args)
        except Exception as error:  # pragma: no cover - execute_tool already guards
            outcome = {"ok": False, "error": "TOOL_ERROR", "message": str(error)[:200]}

        # The model may relay this sentence to the user, so it must not name an
        # environment variable, a credential or a filesystem path.
        outcome = sanitize_observation(outcome)

        if name == "read_product_evidence" and outcome.get("ok"):
            # Remember every product that was actually read: a comparison reply spans
            # several models, and prose validation must not treat the others as unknown.
            product_id = str(outcome.get("product_id") or "")
            if product_id and product_id not in self.ctx.read_product_ids:
                self.ctx.read_product_ids.append(product_id)

        if name in WRITE_TOOLS and call_uid:
            state = ledger.EFFECT_COMPLETED if outcome.get("ok") else ledger.EFFECT_FAILED
            ledger.effect_complete(self.ctx.db, call_uid, outcome, state=state)
        return outcome


def parse_tool_arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}