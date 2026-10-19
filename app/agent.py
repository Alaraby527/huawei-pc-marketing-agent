"""Model-driven agent loop over the Rednote tool registry.

The chat message goes in; the model decides which tools to call. Tool calls
are executed server-side with workspace scope, and the trace is returned so
the UI can show what happened. The loop is bounded and never claims a publish
result the tools did not verify.
"""
from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from . import tools
from .model import ModelError, complete_json
from .rednote import WorkspaceContext, WorkspaceError

class AgentTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=2000)
    conversation_id: str | None = Field(default=None, max_length=64)
    model: str | None = Field(default=None, max_length=80)


SYSTEM_PROMPT = """你是华为 PC 小红书数码运营工作区的执行 Agent。你只能通过调用给定工具完成用户目标。
规则：
1. 只能调用 tools 列表中存在的工具；参数必须符合 JSON Schema。
2. 每一步只输出一个 JSON 对象：{"thought":"","tool":"工具名或null","args":{},"reply":"给用户的最终回复"}。
   - 需要调用工具时：tool 填工具名，args 填参数，reply 留空。
   - 信息不足或任务完成时：tool 为 null，reply 写清楚下一步需要用户提供什么。
3. 产品事实必须来自简报中的已核实事实；不得编造参数、价格、促销、销量、排名或用户体验。
4. 发布：没有有效审批不能创建发布计划；连接器写发布未接通时如实说明，改用人工路径；结果不明不自动重发。
5. 热点只是切入线索，不是产品事实；不得把公开热榜称为官方实时榜。
6. 工具返回 error 时，读取 message 并调整；不要重复同样的失败调用。
"""

SCHEMA_HINT = "只输出一个 JSON 对象，字段为 thought/tool/args/reply；tool 必须是工具名或 null。"

STATE_KEYS = ("content_item_id", "snapshot_id", "selection_id", "board_id", "document_id",
              "revision_id", "approval_id", "plan_id")


def _summary(outcome) -> str:
    if not isinstance(outcome, dict):
        return str(outcome)[:120]
    if outcome.get("error"):
        return f"{outcome['error']}: {outcome.get('message', '')}"[:160]
    for key in ("content_item_id", "board_id", "document_id", "revision_id", "approval_id",
                "plan_id", "snapshot_id", "selection_id", "status", "message"):
        if outcome.get(key):
            return f"{key}={outcome[key]}"[:160]
    if outcome.get("directions"):
        return f"{len(outcome['directions'])} 个创意方向"
    if outcome.get("candidates"):
        return f"{len(outcome['candidates'])} 张样张"
    return "ok"


def run_agent(db: Session, ctx: WorkspaceContext, message: str,
              conversation_id: str | None = None, model: str | None = None,
              max_steps: int = 6) -> dict:
    state = {key: None for key in STATE_KEYS}
    trace: list[dict] = []
    messages = [{"role": "user", "content": message}]
    for step in range(max_steps):
        payload = {"conversation_id": conversation_id, "state": state,
                   "messages": messages[-8:], "tools": tools.TOOL_SPECS}
        try:
            result, _meta = complete_json(SYSTEM_PROMPT, payload, schema_hint=SCHEMA_HINT, model=model)
        except ModelError as error:
            return {"reply": "当前模型不可用，无法自主调用工具（" + str(error) + "）。可以继续使用规则链路，或检查模型配置。",
                    "tool_trace": trace, "state": state, "engine": "agent-unavailable"}
        if not isinstance(result, dict):
            return {"reply": "模型返回了无法解析的结果，请重试或改用规则链路。",
                    "tool_trace": trace, "state": state, "engine": "agent-invalid"}
        tool_name = result.get("tool")
        if not tool_name:
            return {"reply": str(result.get("reply") or "请补充需要的信息。"),
                    "tool_trace": trace, "state": state, "engine": "agent"}
        args = result.get("args") if isinstance(result.get("args"), dict) else {}
        try:
            outcome = tools.dispatch(db, ctx, tool_name, args)
        except WorkspaceError as error:
            outcome = {"error": error.code, "message": error.message}
        except Exception as error:  # tool bugs must not kill the loop
            outcome = {"error": "TOOL_ERROR", "message": str(error)[:200]}
        trace.append({"step": step + 1, "tool": tool_name, "args": args,
                      "ok": not (isinstance(outcome, dict) and outcome.get("error")),
                      "summary": _summary(outcome)})
        if isinstance(outcome, dict):
            for key in STATE_KEYS:
                if outcome.get(key):
                    state[key] = outcome[key]
            if outcome.get("directions"):
                state["direction_ids"] = [row["id"] for row in outcome["directions"]]
        messages.append({"role": "assistant", "content": json.dumps({"tool": tool_name, "args": args}, ensure_ascii=False)})
        messages.append({"role": "user",
                         "content": "工具结果：" + json.dumps(outcome, ensure_ascii=False, default=str)[:2000]})
    return {"reply": "已达到工具调用上限（" + str(max_steps) + " 步），请检查当前进度后继续。",
            "tool_trace": trace, "state": state, "engine": "agent"}
