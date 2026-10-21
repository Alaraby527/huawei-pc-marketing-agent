"""Persistable graph state.

Mirrors ``contracts/graph-state.schema.json`` from the remediation package. Everything
here must survive a checkpoint round-trip as JSON, so it stores ids, hashes and small
projections only — never a DB session, model client, credential or image blob.
"""
from __future__ import annotations

from typing import Any, TypedDict

SCHEMA_VERSION = "marketing-lg-v1"

# Terminal / semi-terminal run states. ``partial`` is deliberately distinct from
# ``completed`` so a budget-exhausted run cannot be counted as success.
STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_WAITING_USER = "waiting_user"
STATUS_COMPLETED = "completed"
STATUS_PARTIAL = "partial"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"
STATUS_NEEDS_RECONCILIATION = "needs_reconciliation"

FINAL_STATUSES = frozenset({
    STATUS_COMPLETED, STATUS_PARTIAL, STATUS_FAILED,
    STATUS_CANCELLED, STATUS_NEEDS_RECONCILIATION,
})


class MarketingState(TypedDict, total=False):
    # --- identity -----------------------------------------------------------
    schema_version: str
    conversation_id: str
    run_id: str
    thread_id: str

    # --- conversation projection (transport shape, no secrets) --------------
    messages: list[dict[str, Any]]
    user_requirements: dict[str, Any]
    assumptions: list[str]

    # --- evidence / artifact references (not the payloads themselves) -------
    evidence_refs: list[dict[str, Any]]
    artifact_ref: dict[str, Any] | None
    candidates: list[dict[str, Any]]
    images: list[dict[str, Any]]

    # --- control ------------------------------------------------------------
    pending_calls: list[dict[str, Any]]
    pending_question_ref: str | None
    last_observation: dict[str, Any] | None
    repair_count: int
    decision_index: int
    protocol_mode: str

    # --- terminal -----------------------------------------------------------
    status: str
    output_candidate: dict[str, Any] | None
    stop_reason: str


def initial_state(*, conversation_id: str, run_id: str, thread_id: str,
                  user_text: str, requirements: dict[str, Any] | None = None) -> MarketingState:
    """Fresh state for one user request (one AgentRun)."""
    return MarketingState(
        schema_version=SCHEMA_VERSION,
        conversation_id=conversation_id,
        run_id=run_id,
        thread_id=thread_id,
        messages=[{"role": "user", "content": user_text}],
        user_requirements=dict(requirements or {}),
        assumptions=[],
        evidence_refs=[],
        artifact_ref=None,
        candidates=[],
        images=[],
        pending_calls=[],
        pending_question_ref=None,
        last_observation=None,
        repair_count=0,
        decision_index=0,
        protocol_mode="native_tools",
        status=STATUS_RUNNING,
        output_candidate=None,
        stop_reason="",
    )