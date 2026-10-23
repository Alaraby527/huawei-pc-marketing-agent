"""The general execution graph.

A single generic loop — ``decide -> (execute_tools | wait_for_user) -> decide`` with a
``finalize`` exit. It encodes *no* marketing funnel: there is no intake/recommend/plan
stage and no fixed step count. The main model decides whether to ask, query, write,
revise or deliver; the backend decides what is true, permitted and affordable.

Two pseudo-changes this design refuses (both are called out in the remediation plan):

* wrapping the legacy ``_guided_turn`` in one node;
* wrapping the legacy 12-step ``for`` loop in an outer graph.

If the decision of "what next" still lives in the old code, the migration has not
happened.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from sqlalchemy.exc import SQLAlchemyError

from .. import agent_runtime
from . import ledger
from .context import RuntimeContext
from .content_policy import (EXPERIENCE_NOTICE, PLACEHOLDER_NOTICE,
                             UNVERIFIED_NUMBER_NOTICE,
                             fabricated_experience, is_refusal_context,
                             review_notice, unresolved_placeholders,
                             unverified_product_numbers,
                             unverified_reply_notice)
from .errors import error_marker, public_error
from .state import (FINAL_STATUSES, STATUS_CANCELLED, STATUS_COMPLETED, STATUS_FAILED,
                    STATUS_NEEDS_RECONCILIATION, STATUS_PARTIAL, STATUS_RUNNING,
                    MarketingState)
from .model_gateway import REVIEWER_SYSTEM_PROMPT
from .tool_gateway import parse_tool_arguments

# Tools whose outcome must be reflected back into state for later steps.
EVIDENCE_TOOLS = frozenset({"read_product_evidence", "search_products", "compare_products",
                            "find_product_images", "read_product_fit", "read_field_matrix"})
ARTIFACT_TOOLS = frozenset({"save_artifact"})

# Per-tool-result cap on what is replayed into the transcript. Several bulky results
# previously pushed a decide step past 8.7 K prompt tokens, and the gateway then closed
# the connection without a response. Cuts are marked, never silent.
TOOL_RESULT_LIMIT = 12000


def build_graph(saver, ctx: RuntimeContext):
    """Compile the graph for one request, closing over its trusted runtime context."""

    async def decide(state: MarketingState) -> dict[str, Any]:
        if ctx.cancel_check():
            return {"status": STATUS_CANCELLED, "pending_calls": [], "stop_reason": "cancelled",
                    "last_observation": {"ok": False, "error": "CANCELLED"}}

        index = int(state.get("decision_index") or 0) + 1
        if index > ctx.max_decisions:
            # Budget exhaustion is a partial delivery, never an unconditional success.
            return {"status": STATUS_PARTIAL, "pending_calls": [],
                    "stop_reason": "model_call_budget", "decision_index": index - 1}
        if ctx.out_of_time():
            return {"status": STATUS_PARTIAL, "pending_calls": [],
                    "stop_reason": "time_budget", "decision_index": index - 1}

        # The knowledge base is NOT loaded here. The model decides whether it needs
        # evidence at all (a title or a creative brief does not), and any KB failure
        # surfaces as one tool error the model can react to — instead of killing the
        # whole turn before the first sentence is ever written.
        messages = list(state.get("messages") or [])
        call = asyncio.create_task(asyncio.to_thread(
            ctx.gateway.decide, messages, agent_runtime.NATIVE_TOOL_SPECS,
            decision_index=index))
        done, _pending = await asyncio.wait({call}, timeout=ctx.model_call_timeout)
        if done:
            try:
                decision, error = call.result()
            except SQLAlchemyError:
                # A storage failure is not a failed decision. Swallowing it here leaves the
                # session unusable, so the run's own terminal write fails as well and the
                # request becomes a 500 with the run stuck in `running`.
                raise
            except Exception as exc:  # noqa: BLE001 - reported as a failed decision
                decision, error = None, str(exc)
        else:
            # asyncio.wait (unlike asyncio.wait_for) does not wait for the cancelled
            # task, so the run stays bounded even when a blocked blocking-call thread
            # cannot be interrupted. The thread finishes on its own and its result is
            # discarded; its failure is swallowed so it cannot surface later.
            call.cancel()
            call.add_done_callback(
                lambda task: task.exception() if not task.cancelled() else None)
            decision, error = None, "模型调用超过总时限"

        if error or decision is None:
            error = error or "模型调用超过总时限"
            return {"status": STATUS_FAILED, "pending_calls": [], "decision_index": index,
                    "stop_reason": error_marker(error),
                    "output_candidate": {"reply": public_error(error)}}

        base = {"decision_index": index, "protocol_mode": decision.protocol_mode,
                "status": STATUS_RUNNING}

        if not decision.tool_calls:
            return {**base, "pending_calls": [],
                    "output_candidate": {"reply": decision.content}}

        calls = []
        for call in decision.tool_calls[:4]:
            function = call.get("function") or {}
            calls.append({"call_uid": str(call.get("id") or f"call-{index}-{len(calls)}"),
                          "provider_call_id": str(call.get("id") or ""),
                          "name": str(function.get("name") or ""),
                          "arguments": function.get("arguments") or "{}"})
        ask = next((call for call in calls if call["name"] == "ask_user"), None)
        if ask:
            arguments = parse_tool_arguments(ask["arguments"])
            question = str(arguments.get("question") or "还需要确认一件事：")
            options = arguments.get("options") or []
            return {**base, "pending_calls": [ask],
                    "pending_question_ref": json.dumps({"question": question,
                                                        "options": options},
                                                       ensure_ascii=False)}
        return {**base, "pending_calls": calls, "pending_question_ref": None}

    async def execute_tools(state: MarketingState) -> dict[str, Any]:
        if ctx.cancel_check():
            return {"status": STATUS_CANCELLED, "pending_calls": [], "stop_reason": "cancelled"}

        messages = list(state.get("messages") or [])
        evidence_refs = list(state.get("evidence_refs") or [])
        artifact_ref = state.get("artifact_ref")
        candidates = list(state.get("candidates") or [])
        images = list(state.get("images") or [])
        repair_count = int(state.get("repair_count") or 0)
        observations: list[dict] = []
        status = STATUS_RUNNING
        stop_reason = ""

        # Tool fan-out needs its own ceiling: the model-call cap does not bound how many
        # tools each decision may fire. Counting replayed tool results is the exact
        # measure of what has already been spent.
        pending = list(state.get("pending_calls") or [])
        used_tools = sum(1 for message in messages if message.get("role") == "tool")
        remaining_tools = ctx.max_tool_calls - used_tools
        if remaining_tools <= 0:
            return {"status": STATUS_PARTIAL, "pending_calls": [],
                    "stop_reason": "tool_budget_exhausted",
                    "output_candidate": {
                        "reply": "本轮工具调用已达上限（" + str(ctx.max_tool_calls) +
                                 " 次），已停止继续取数；现有内容按未完成处理，"
                                 "可以让我接着做剩下的部分。"}}
        if len(pending) > remaining_tools:
            # Trim the batch before the assistant turn is built, so every call the
            # provider sees still has exactly one result.
            pending = pending[:remaining_tools]

        # Assistant turn carrying every tool call of this batch, so the provider sees a
        # complete call/result pairing (a missing tool_result is a 400 from the gateway).
        assistant_calls = [{"id": call["call_uid"], "type": "function",
                            "function": {"name": call["name"],
                                         "arguments": call["arguments"]}}
                           for call in pending]
        messages.append({"role": "assistant", "content": None, "tool_calls": assistant_calls})

        # Writes are serialized: one SQLAlchemy session is not a concurrency primitive.
        for call in pending:
            name = call["name"]
            arguments = parse_tool_arguments(call["arguments"])
            outcome = await ctx.tools.run(name, arguments, call_uid=call["call_uid"])
            observations.append({"tool": name, "call_uid": call["call_uid"], **outcome})
            _commit_quietly(ctx.db)
            serialized = json.dumps(outcome, ensure_ascii=False, default=str)
            if len(serialized) > TOOL_RESULT_LIMIT:
                # Mark the cut: a silently truncated tool result reads as complete and
                # made the model reason from a partial document.
                serialized = (serialized[:TOOL_RESULT_LIMIT] +
                              "…[工具结果已截断，原文 " + str(len(serialized)) +
                              " 字符]")
            messages.append({"role": "tool", "tool_call_id": call["call_uid"],
                             "content": serialized})

            if outcome.get("status") == STATUS_NEEDS_RECONCILIATION or \
                    outcome.get("error") == "OUTCOME_UNKNOWN":
                status = STATUS_NEEDS_RECONCILIATION
                stop_reason = "upstream_outcome_unknown"
                break

            if name in ARTIFACT_TOOLS and outcome.get("ok"):
                artifact_ref = {"artifact_id": outcome.get("artifact_id"),
                                "revision": outcome.get("revision"),
                                "content_hash": outcome.get("content_hash")}
                check = outcome.get("check") or {}
                if check and not check.get("passed", True):
                    repair_count += 1

            if name == "read_product_evidence" and outcome.get("ok"):
                product_id = str(outcome.get("product_id") or "")
                if product_id and all(ref.get("product_id") != product_id
                                      for ref in evidence_refs):
                    evidence_refs.append({"snapshot_id": product_id,
                                          "source_kind": _source_kind(ctx),
                                          "source_hash": _evidence_hash(outcome)})

            if name == "search_products" and outcome.get("ok") and outcome.get("products"):
                candidates = [{"product_id": str(item.get("product_id") or ""),
                               "model": str(item.get("model") or ""),
                               "facts_count": int(item.get("facts_count") or 0),
                               "reason": "", "facts": []}
                              for item in outcome["products"][:3]]
                for product in outcome["products"][:3]:
                    product_id = str(product.get("product_id") or "")
                    if product_id and all(ref.get("product_id") != product_id
                                          for ref in evidence_refs):
                        evidence_refs.append({"snapshot_id": product_id,
                                              "source_kind": _source_kind(ctx),
                                              "source_hash": ""})
                if outcome.get("evidence") and not ctx.tool_ctx.evidence_read:
                    ctx.tool_ctx.evidence_read = True
                    ctx.tool_ctx.last_search = outcome

            if name == "find_product_images" and outcome.get("ok"):
                rows = outcome.get("images") or []
                normalized = []
                for row in rows[:6]:
                    if isinstance(row, dict):
                        normalized.append(row)
                    elif isinstance(row, str) and row:
                        normalized.append({"image_id": "", "caption": "",
                                           "access_url": row, "rendered": True})
                if normalized:
                    images = normalized

        if status == STATUS_NEEDS_RECONCILIATION:
            return {"messages": messages, "evidence_refs": evidence_refs,
                    "artifact_ref": artifact_ref, "candidates": candidates,
                    "images": images, "pending_calls": [], "status": status,
                    "stop_reason": stop_reason, "repair_count": repair_count,
                    "last_observation": observations[-1] if observations else None}

        return {"messages": messages, "evidence_refs": evidence_refs,
                "artifact_ref": artifact_ref, "candidates": candidates, "images": images,
                "pending_calls": [], "status": STATUS_RUNNING, "repair_count": repair_count,
                "last_observation": observations[-1] if observations else None}

    async def wait_for_user(state: MarketingState) -> dict[str, Any]:
        """Pause for one question. No paid call and no business write happens here."""
        payload = json.loads(state.get("pending_question_ref") or "{}")
        answer = interrupt({"question": payload.get("question", ""),
                            "options": payload.get("options") or [],
                            "free_text_allowed": True})
        messages = list(state.get("messages") or [])
        messages.append({"role": "user", "content": str(answer)})
        return {"messages": messages, "pending_calls": [], "pending_question_ref": None,
                "status": STATUS_RUNNING}

    async def finalize(state: MarketingState) -> dict[str, Any]:
        """Unified output fence: one check for reply text, artifact and export surfaces."""
        status = state.get("status") or STATUS_RUNNING
        candidate = dict(state.get("output_candidate") or {})
        reply = str(candidate.get("reply") or "")

        if status not in FINAL_STATUSES:
            status = STATUS_COMPLETED

        if status == STATUS_COMPLETED:
            artifact_ref = state.get("artifact_ref")
            if artifact_ref and artifact_ref.get("artifact_id"):
                check = await asyncio.to_thread(_recheck_artifact, ctx, artifact_ref)
                if check and not check.get("passed", True):
                    # Never leave a known-bad claim in the delivered text behind a
                    # disclaimer; report the specific problem instead.
                    status = STATUS_PARTIAL
                    errors = check.get("errors") or []
                    detail = "；".join(str(item.get("message", "")) for item in errors[:2])
                    reply = (reply + " 交付物未通过事实检查，已保留为待修正版本。" +
                             (detail or ""))
                    candidate["check"] = check

            if reply:
                violation = _reply_fact_violation(ctx, reply)
                if violation:
                    status = STATUS_PARTIAL
                    # Flag only. Rewriting prose silently replaced another model's real
                    # figures with the first model's values in a cross-model comparison.
                    candidate["unsupported_numbers"] = violation["unsupported"]
                    reply = reply + "\n\n" + unverified_reply_notice(
                        violation["unsupported"][:4])

            if reply:
                # Fabricated lived experience is forbidden by the platform rules but was
                # never enforced. Flag it instead of shipping it as verified content.
                # Checked independently of the experience fence: a draft can both invent an
                # experience and leave "价格待定" in place, and the second one is a delivery
                # gate rather than a style issue.
                placeholders = unresolved_placeholders(reply)
                if placeholders:
                    status = STATUS_PARTIAL
                    candidate["unresolved_placeholders"] = placeholders[:8]
                    reply = reply + "\n\n" + PLACEHOLDER_NOTICE
                markers = fabricated_experience(reply)
                if markers:
                    status = STATUS_PARTIAL
                    candidate["unverified_experience"] = markers
                    reply = reply + "\n\n" + EXPERIENCE_NOTICE
                elif not ctx.tool_ctx.evidence_read and not placeholders:
                    numbers = unverified_product_numbers(reply)
                    if numbers:
                        status = STATUS_PARTIAL
                        candidate["unverified_numbers"] = numbers
                        reply = reply + "\n\n" + UNVERIFIED_NUMBER_NOTICE

        candidate["reply"] = reply
        # A proposed delivery surface travels with the result so the UI can ask the user.
        suggestion = getattr(ctx.tool_ctx, "surface_suggestion", None)
        if suggestion:
            candidate["surface_suggestion"] = suggestion
        return {"status": status, "output_candidate": candidate,
                "stop_reason": state.get("stop_reason") or ""}

    async def review(state: MarketingState) -> dict[str, Any]:
        """Independent review of the delivered text (plan G7 / T08).

        Two properties matter and both are deliberate:

        * **Separate context.** It receives the delivered text plus the allowed facts and
          conditions — never the author's reasoning or self-assessment. It also checks the
          whole text, not only the claims the author chose to label.
        * **An unmet check is reported, not implied.** If the budget is already spent the
          review records ``not_run``, and the delivery can then never be called
          ``review_ready``.
        """
        status = state.get("status") or STATUS_RUNNING
        candidate = dict(state.get("output_candidate") or {})
        reply = str(candidate.get("reply") or "")
        if status not in (STATUS_COMPLETED, STATUS_PARTIAL) or not reply:
            return {}

        spent = ledger.attempts_for_run(ctx.db, ctx.run.id)
        if spent >= ctx.max_decisions:
            candidate["review"] = {"status": "not_run", "reason": "model_call_budget",
                                   "attempts": spent}
            return {"output_candidate": candidate}

        payload = _review_payload(ctx, state, reply)
        call = asyncio.create_task(asyncio.to_thread(
            ctx.gateway.compose, REVIEWER_SYSTEM_PROMPT,
            [{"role": "user", "content": payload}],
            kind="review", decision_index=int(state.get("decision_index") or 0) + 1))
        done, _pending = await asyncio.wait({call}, timeout=ctx.model_call_timeout)
        if not done:
            call.cancel()
            call.add_done_callback(
                lambda task: task.exception() if not task.cancelled() else None)
            candidate["review"] = {"status": "not_run", "reason": "review_timeout"}
            return {"output_candidate": candidate}
        try:
            result, _error = call.result()
        except Exception as exc:  # noqa: BLE001 - a failed review is reported as not_run
            candidate["review"] = {"status": "not_run", "reason": "model_error",
                                   "detail": type(exc).__name__}
            return {"output_candidate": candidate}

        report = _review_report(result)
        candidate["review"] = report
        blocking = [item for item in report["issues"]
                    if str(item.get("severity") or "").lower() in ("high", "critical")]
        refused = report["verdict"] in ("block", "needs_input", "human_review",
                                        "choose_new_plan")
        if blocking or refused:
            status = STATUS_PARTIAL
            detail = "；".join(str(item.get("reason") or item.get("excerpt") or "")[:60]
                              for item in (blocking or report["issues"])[:3])
            candidate["reply"] = reply + "\n\n" + review_notice(
                len(blocking) or report["issue_count"], detail)
        return {"status": status, "output_candidate": candidate}

    def route_after_decide(state: MarketingState) -> str:
        if state.get("status") in FINAL_STATUSES:
            return "finalize"
        calls = state.get("pending_calls") or []
        if not calls:
            return "finalize"
        return "wait_for_user" if calls[0]["name"] == "ask_user" else "execute_tools"

    def route_after_tools(state: MarketingState) -> str:
        if state.get("status") in {STATUS_CANCELLED, STATUS_NEEDS_RECONCILIATION}:
            return "finalize"
        return "decide"

    graph = StateGraph(MarketingState)
    graph.add_node("decide", decide)
    graph.add_node("execute_tools", execute_tools)
    graph.add_node("wait_for_user", wait_for_user)
    graph.add_node("finalize", finalize)
    graph.add_node("review", review)
    graph.add_edge(START, "decide")
    graph.add_conditional_edges("decide", route_after_decide,
                               {"execute_tools": "execute_tools",
                                "wait_for_user": "wait_for_user",
                                "finalize": "finalize"})
    graph.add_conditional_edges("execute_tools", route_after_tools,
                               {"decide": "decide", "finalize": "finalize"})
    graph.add_edge("wait_for_user", "decide")
    # The reviewer runs on the finalised text, after the programmatic fence, so it sees
    # exactly what would be delivered.
    graph.add_edge("finalize", "review")
    graph.add_edge("review", END)
    return graph.compile(checkpointer=saver)


def _commit_quietly(db) -> None:
    """Keep the write transaction short: SQLite has a single writer.

    Tool executions write ledger rows; leaving them uncommitted holds the write lock for
    the whole run and makes concurrent runs fail instead of waiting.
    """
    try:
        db.commit()
    except Exception:  # noqa: BLE001 - the terminal write reports a persistent problem
        db.rollback()


def _allowed_fact_lines(ctx: RuntimeContext, limit: int = 40) -> list[str]:
    """The facts the delivery is allowed to rely on, for the reviewer to judge against."""
    lines: list[str] = []
    catalog = ctx.tool_ctx.catalog
    if catalog is None:
        return lines
    for product_id in list(getattr(ctx.tool_ctx, "read_product_ids", []) or []):
        product = agent_runtime._resolve_product(catalog, product_id, ctx.tool_ctx)
        if not product:
            continue
        for fact in product.facts:
            text = str(getattr(fact, "text", "") or "").replace("\n", " ")
            condition = str(getattr(fact, "conditions", "") or "").replace("\n", " ")
            lines.append(product.model + "｜" + text[:160] +
                         (("（条件：" + condition[:80] + "）") if condition else ""))
            if len(lines) >= limit:
                return lines
    return lines


def _review_payload(ctx: RuntimeContext, state: MarketingState, reply: str) -> str:
    """Build the reviewer's input: the delivered text and the allowed facts only."""
    parts = ["【待交付文案】", reply[:6000]]
    artifact_ref = state.get("artifact_ref") or {}
    if artifact_ref.get("artifact_id"):
        loaded = agent_runtime._load_artifact(ctx.tool_ctx, dict(artifact_ref))
        if loaded.get("ok"):
            parts += ["", "【交付物正文】",
                      json.dumps(loaded.get("content"), ensure_ascii=False)[:6000]]
    facts = _allowed_fact_lines(ctx)
    if facts:
        parts += ["", "【本次允许使用的事实】"] + facts
    else:
        parts += ["", "【本次允许使用的事实】", "（本轮没有读到任何产品事实）"]
    return "\n".join(parts)


def _review_report(result) -> dict:
    """Normalise the reviewer output; a malformed review is recorded as unknown."""
    if not isinstance(result, dict):
        return {"status": "ran", "verdict": "unknown", "issue_count": 0, "blocking": 0,
                "issues": [], "checked": [], "malformed": True}
    issues = [item for item in (result.get("issues") or []) if isinstance(item, dict)]
    return {"status": "ran",
            "verdict": str(result.get("verdict") or "unknown"),
            "issue_count": len(issues),
            "blocking": len([item for item in issues
                             if str(item.get("severity") or "").lower() in ("high",
                                                                            "critical")]),
            "issues": issues[:6],
            "checked": [str(item) for item in (result.get("checked") or [])][:10]}


def _source_kind(ctx: RuntimeContext) -> str:
    client = getattr(ctx.tool_ctx.catalog, "client", None)
    return str(getattr(client, "source_kind", None) or "ima")


def _evidence_hash(outcome: dict) -> str:
    facts = outcome.get("facts") or []
    return ledger.payload_hash(facts)[:32] if facts else ""


def _records_for(ctx: RuntimeContext, product_id: str):
    from ..grounding import records_from_facts
    product = agent_runtime._resolve_product(ctx.tool_ctx.catalog, product_id, ctx.tool_ctx)
    if not product:
        return None, None
    return product, records_from_facts(product.facts, product.product_id, "model-level")


def _recheck_artifact(ctx: RuntimeContext, artifact_ref: dict) -> dict | None:
    loaded = agent_runtime._load_artifact(ctx.tool_ctx, {
        "artifact_id": artifact_ref.get("artifact_id"),
        "revision": artifact_ref.get("revision")})
    if not loaded.get("ok"):
        return None
    product_id = str((loaded.get("content") or {}).get("product_id") or "")
    if not product_id:
        return {"passed": True, "errors": [], "warnings": [], "checked": 0,
                "note": "未绑定产品事实，仅做创意内容交付"}
    _, records = _records_for(ctx, product_id)
    if records is None:
        return None
    from ..grounding import check_artifact
    return check_artifact(loaded["content"], records)


def _reply_fact_violation(ctx: RuntimeContext, reply: str) -> dict | None:
    """Flag numbers in the *reply text* that no read product supports.

    Two deliberate choices, both learned the hard way:

    * **No rewriting.** An earlier version ran ``sanitize_text`` over the prose, which
      corrupted a legitimate cross-model comparison — it replaced another model's real
      figures with the first model's values ("D 16 约 1.68–1.33 kg / 1.32 kg"). Structured
      artifact content is still repaired by the artifact path; prose is only flagged.
    * **Union of every product read**, not just the first: a reply may compare several
      models, and their figures are equally supported.
    """
    if not ctx.tool_ctx.evidence_read:
        return None
    from ..grounding import extract_claims, records_from_facts

    records: list = []
    seen: set[str] = set()
    product_ids = list(getattr(ctx.tool_ctx, "read_product_ids", []) or [])
    if not product_ids:
        products = (ctx.tool_ctx.last_search or {}).get("products") or []
        product_ids = [str(item.get("product_id") or "") for item in products]
    for product_id in product_ids:
        if not product_id or product_id in seen or ctx.tool_ctx.catalog is None:
            continue
        seen.add(product_id)
        product = agent_runtime._resolve_product(ctx.tool_ctx.catalog, product_id,
                                                 ctx.tool_ctx)
        if product:
            records.extend(records_from_facts(product.facts, product.product_id,
                                              "model-level"))
    if not records:
        return None
    claims = extract_claims(reply)
    if not claims:
        return None
    from ..grounding import check_text
    report = check_text(reply, records)
    unsupported = []
    for item in report["errors"]:
        if item.get("code") != "FACT_NOT_FOUND":
            continue
        span = item.get("span") or [0, 0]
        # Do not flag a number the reply is explicitly refusing or asking about: that
        # turned correct refusals into `partial` runs.
        if is_refusal_context(reply, int(span[0]), int(span[1])):
            continue
        claim = str(item.get("claim") or "")
        if claim:
            unsupported.append(claim)
    if not unsupported:
        return None
    return {"reply": reply,
            "unsupported": sorted(set(unsupported)),
            "detail": "回复中出现知识库未核到的数字，已标记未核实（未自动改写）"}