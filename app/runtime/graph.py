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

from .. import agent_runtime
from . import ledger
from .context import RuntimeContext
from .content_policy import (EXPERIENCE_NOTICE, UNVERIFIED_NUMBER_NOTICE,
                             fabricated_experience, is_refusal_context,
                             unverified_product_numbers, unverified_reply_notice)
from .errors import error_marker, public_error
from .state import (FINAL_STATUSES, STATUS_CANCELLED, STATUS_COMPLETED, STATUS_FAILED,
                    STATUS_NEEDS_RECONCILIATION, STATUS_PARTIAL, STATUS_RUNNING,
                    MarketingState)
from .tool_gateway import parse_tool_arguments

# Tools whose outcome must be reflected back into state for later steps.
EVIDENCE_TOOLS = frozenset({"read_product_evidence", "search_products", "compare_products",
                            "find_product_images"})
ARTIFACT_TOOLS = frozenset({"save_artifact"})


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

        # Assistant turn carrying every tool call of this batch, so the provider sees a
        # complete call/result pairing (a missing tool_result is a 400 from the gateway).
        assistant_calls = [{"id": call["call_uid"], "type": "function",
                            "function": {"name": call["name"],
                                         "arguments": call["arguments"]}}
                           for call in (state.get("pending_calls") or [])]
        messages.append({"role": "assistant", "content": None, "tool_calls": assistant_calls})

        # Writes are serialized: one SQLAlchemy session is not a concurrency primitive.
        for call in (state.get("pending_calls") or []):
            name = call["name"]
            arguments = parse_tool_arguments(call["arguments"])
            outcome = await ctx.tools.run(name, arguments, call_uid=call["call_uid"])
            observations.append({"tool": name, "call_uid": call["call_uid"], **outcome})
            messages.append({"role": "tool", "tool_call_id": call["call_uid"],
                             "content": json.dumps(outcome, ensure_ascii=False,
                                                   default=str)[:12000]})

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

            if name in ("find_product_images", "render_visuals") and outcome.get("ok"):
                rows = outcome.get("images") or []
                if rows and isinstance(rows[0], dict):
                    images = rows[:6]

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
                markers = fabricated_experience(reply)
                if markers:
                    status = STATUS_PARTIAL
                    candidate["unverified_experience"] = markers
                    reply = reply + "\n\n" + EXPERIENCE_NOTICE
                elif not ctx.tool_ctx.evidence_read:
                    numbers = unverified_product_numbers(reply)
                    if numbers:
                        status = STATUS_PARTIAL
                        candidate["unverified_numbers"] = numbers
                        reply = reply + "\n\n" + UNVERIFIED_NUMBER_NOTICE

        candidate["reply"] = reply
        return {"status": status, "output_candidate": candidate,
                "stop_reason": state.get("stop_reason") or ""}

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
    graph.add_edge(START, "decide")
    graph.add_conditional_edges("decide", route_after_decide,
                               {"execute_tools": "execute_tools",
                                "wait_for_user": "wait_for_user",
                                "finalize": "finalize"})
    graph.add_conditional_edges("execute_tools", route_after_tools,
                               {"decide": "decide", "finalize": "finalize"})
    graph.add_edge("wait_for_user", "decide")
    graph.add_edge("finalize", END)
    return graph.compile(checkpointer=saver)


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