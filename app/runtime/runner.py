"""Entry point for ``langgraph_autonomous_v1``.

Wire-compatible with the existing v2 API: the caller still gets a payload shaped like
``agent_runtime.run_payload`` (reply / events / usage / status), so ``main.py``, the
task-history endpoints and the current frontend keep working.

The checkpointer is a long-lived, process-wide saver — it is *not* re-created and
destroyed per request. The compiled graph is cheap and is built per request so it can
close over that request's trusted runtime context without smuggling a DB session into
checkpointed state.
"""
from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import aiosqlite
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import agent_runtime, db as db_layer, models
from . import RUNNER_VERSION, ledger
from .context import RuntimeContext, build_context
from .errors import error_marker, public_error
from .graph import build_graph
from .state import (STATUS_CANCELLED, STATUS_COMPLETED, STATUS_FAILED,
                    STATUS_NEEDS_RECONCILIATION, STATUS_RUNNING, STATUS_WAITING_USER,
                    initial_state)

_savers: dict[tuple[int, str], tuple[AsyncSqliteSaver, aiosqlite.Connection]] = {}
_init_locks: dict[int, asyncio.Lock] = {}
_conversation_locks: dict[tuple[int, str], asyncio.Lock] = {}
_background: dict[str, asyncio.Task] = {}


def _conversation_lock(conversation_id: str) -> asyncio.Lock:
    """Serialize execution of one conversation within this process.

    Keyed by event loop as well, for the same reason as the init lock. This is a local
    lease only; multiple workers would need real coordination.
    """
    key = (id(asyncio.get_running_loop()), conversation_id)
    lock = _conversation_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _conversation_locks[key] = lock
    return lock


def _init_lock(loop) -> asyncio.Lock:
    """One lock per event loop.

    A module-level Lock created on one loop and awaited on another raises
    ``is bound to a different event loop``. Production runs a single loop, but a test
    harness (or an embedded runner) may not, so the lock is per loop rather than global.
    """
    lock = _init_locks.get(id(loop))
    if lock is None:
        lock = asyncio.Lock()
        _init_locks[id(loop)] = lock
    return lock


class RunnerError(RuntimeError):
    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.message = message
        self.status = status


def checkpoint_path() -> str:
    """Checkpoints live beside the business database but in their own file.

    Sharing one SQLite file between the SQLAlchemy engine and the saver invites lock
    contention, and the two stores have different lifecycles.
    """
    explicit = (os.getenv("LG_CHECKPOINT_PATH") or "").strip()
    if explicit:
        return explicit
    url = db_layer.database_url()
    if url.startswith("sqlite:///"):
        business = Path(url[len("sqlite:///"):])
        return str(business.parent / "agent_checkpoints.sqlite")
    return str(Path(db_layer.default_sqlite_path()).parent / "agent_checkpoints.sqlite")


async def get_saver() -> AsyncSqliteSaver:
    """Return the saver for the running event loop, opening it once per (loop, path).

    Each loop keeps its own connection and is never torn down by another loop's
    request: closing a connection that an in-flight task is still using raises
    ``Cannot operate on a closed database``. Production runs a single loop and opens
    this once in the application lifespan; the registry exists so scripts and test
    harnesses with several loops stay correct.
    """
    loop = asyncio.get_running_loop()
    path = checkpoint_path()
    key = (id(loop), path)
    entry = _savers.get(key)
    if entry is not None:
        return entry[0]
    async with _init_lock(loop):
        entry = _savers.get(key)
        if entry is not None:
            return entry[0]
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(path)
        saver = AsyncSqliteSaver(conn)
        await saver.setup()
        _savers[key] = (saver, conn)
        return saver


async def init_saver() -> AsyncSqliteSaver:
    """Open the saver at application startup, before the first request."""
    return await get_saver()


async def close_saver() -> None:
    """Close every open saver. Called once, on application shutdown."""
    for saver, conn in list(_savers.values()):
        try:
            await conn.close()
        except Exception:  # noqa: BLE001 - shutdown must not raise
            pass
    _savers.clear()


async def purge_thread(thread_id: str) -> bool:
    """Delete the checkpoint thread for a removed conversation.

    Deleting the business rows without this leaves a resumable orphan checkpoint.
    """
    saver = await get_saver()
    try:
        await saver.adelete_thread(thread_id)
        return True
    except Exception:
        return False


def _thread_id(conversation: models.AgentConversation) -> str:
    return f"{conversation.id}:{conversation.runner_version or RUNNER_VERSION}"


def _interrupt_payload(result) -> dict | None:
    for item in (result or {}).get("__interrupt__") or ():
        value = getattr(item, "value", None)
        if isinstance(value, dict):
            return value
    return None


def _request_hash(text: str, model: str | None) -> str:
    return ledger.payload_hash({"message": text, "model": model or ""})


async def submit(db: Session, conversation: models.AgentConversation, text: str,
                 model: str | None = None, idempotency_key: str | None = None) -> dict:
    """Short transaction: accept the request, persist the run, commit — then return.

    Committing before any model call is what makes a running task observable from
    another connection and what lets a concurrent cancel actually be seen. The old
    request-scoped flow kept everything on ``flush()`` until the response.
    """
    request_hash = _request_hash(text, model)
    key = idempotency_key or ""
    try:
        existing_run_id, replayed = ledger.accept_request(db, conversation.id, key,
                                                          request_hash)
    except ledger.RequestConflict as conflict:
        raise RunnerError(str(conflict), status=409) from None
    if replayed and existing_run_id:
        existing = db.get(models.AgentRun, existing_run_id)
        if existing is not None:
            return {"replayed": True, "run_id": existing_run_id,
                    "payload": agent_runtime.run_payload(db, existing)}

    conversation.revision += 1
    db.add(models.AgentMessage(conversation_id=conversation.id, role="user",
                               content=text, seq=ledger.next_seq(db, conversation.id)))
    run = models.AgentRun(conversation_id=conversation.id, model=model or "",
                          status=STATUS_RUNNING)
    db.add(run)
    db.flush()
    agent_runtime._event(run, "message", role="user", content=text)
    ledger.bind_request_run(db, conversation.id, key, run.id)
    db.commit()
    return {"replayed": False, "run_id": run.id, "payload": None}


async def execute_run(db: Session, conversation: models.AgentConversation,
                      run: models.AgentRun, text: str,
                      model: str | None = None) -> dict:
    """Run the graph for one accepted run and finish it on the given session."""
    ctx = build_context(db, conversation, run, model)
    saver = await get_saver()
    graph = build_graph(saver, ctx)
    config = {"configurable": {"thread_id": _thread_id(conversation)},
              "recursion_limit": max(12, ctx.max_decisions * 4)}

    status = STATUS_RUNNING
    reply = ""
    telemetry = ""
    final_state: dict = {}
    try:
        # One conversation executes at a time in this process. This is a local lease,
        # not a distributed lock: a second worker would need real coordination.
        async with _conversation_lock(conversation.id):
            snapshot = await graph.aget_state(config)
            resuming = bool(getattr(snapshot, "next", ()) or ())
            if resuming:
                # Resume with the validated user text only. No arbitrary state patch is
                # accepted from the client, and the new run id reaches the nodes through
                # the trusted RuntimeContext rather than through checkpointed state.
                result = await graph.ainvoke(Command(resume=text), config, durability="sync")
            else:
                state = initial_state(conversation_id=conversation.id, run_id=run.id,
                                      thread_id=ctx.thread_id, user_text=text)
                result = await graph.ainvoke(state, config, durability="sync")
        paused = _interrupt_payload(result)
        if paused is not None:
            status = STATUS_WAITING_USER
            reply = str(paused.get("question") or "还需要确认一件事：")
            # Carry the question's reference options so the UI can offer them; the user
            # is still free to answer in their own words.
            final_state = {"pending_question": paused}
        else:
            status = str(result.get("status") or STATUS_COMPLETED)
            reply = str((result.get("output_candidate") or {}).get("reply") or "")
            telemetry = str(result.get("stop_reason") or "")
            final_state = dict(result or {})
    except Exception as error:  # noqa: BLE001 - reported honestly, never faked as success
        status = STATUS_FAILED
        telemetry = error_marker(str(error) or type(error).__name__)
        # The operator sees the marker; the user sees a sentence with no credentials,
        # environment variables or filesystem paths in it.
        reply = public_error(str(error))

    _finish_run(db, conversation, run, status, reply, telemetry, ctx, final_state)
    return agent_runtime.run_payload(db, run)


async def run_turn(db: Session, conversation: models.AgentConversation, text: str,
                   model: str | None = None, idempotency_key: str | None = None) -> dict:
    """Blocking entry point, wire-compatible with the previous behaviour."""
    prepared = await submit(db, conversation, text, model, idempotency_key)
    if prepared["replayed"]:
        return prepared["payload"]
    run = db.get(models.AgentRun, prepared["run_id"])
    return await execute_run(db, conversation, run, text, model)


def schedule_run(run_id: str) -> None:
    """Execute a submitted run in the background (``response_mode=async``)."""
    task = asyncio.create_task(_background_run(run_id))
    _background[run_id] = task
    task.add_done_callback(lambda _task: _background.pop(run_id, None))


async def _background_run(run_id: str) -> None:
    """Worker execution on its own session: the request session is already closed."""
    session = db_layer.session_factory()()
    try:
        run = session.get(models.AgentRun, run_id)
        if run is None or run.status != STATUS_RUNNING:
            return
        conversation = session.get(models.AgentConversation, run.conversation_id)
        if conversation is None:
            return
        last_user = session.scalars(select(models.AgentMessage).where(
            models.AgentMessage.conversation_id == conversation.id,
            models.AgentMessage.role == "user").order_by(
            models.AgentMessage.seq.desc())).first()
        text = str(last_user.content if last_user else "")
        await execute_run(session, conversation, run, text, run.model or None)
        session.commit()
    except Exception:  # noqa: BLE001 - a worker failure must not kill the server
        session.rollback()
    finally:
        session.close()


def recover_orphaned_runs(db: Session) -> int:
    """Runs left ``running`` by a previous process cannot be assumed to have finished.

    A restart mid-run loses the in-process worker. Reporting those runs as
    ``needs_reconciliation`` is the honest option: we do not know whether the upstream
    work completed, so we must not silently retry it or call it a success.
    """
    rows = db.query(models.AgentRun).filter(
        models.AgentRun.status == STATUS_RUNNING).all()
    for run in rows:
        run.status = STATUS_NEEDS_RECONCILIATION
        run.stop_reason = "orphaned_by_restart"
    if rows:
        db.flush()
    return len(rows)


def _finish_run(db: Session, conversation: models.AgentConversation, run: models.AgentRun,
                status: str, reply: str, telemetry: str, ctx: RuntimeContext,
                state: dict | None = None) -> None:
    # A cancel is a persisted intent: it can never be flipped back into a success.
    if ledger.fresh_run_status(run.id) == STATUS_CANCELLED and status != STATUS_CANCELLED:
        status = STATUS_CANCELLED
        telemetry = telemetry or "user_cancelled"
        if not reply:
            reply = "已按你的要求取消，本轮不再继续。"

    if reply:
        db.add(models.AgentMessage(conversation_id=conversation.id, role="assistant",
                                   content=reply, seq=ledger.next_seq(db, conversation.id)))
        agent_runtime._event(run, "message", role="assistant", content=reply)

    totals = ledger.usage_totals(db, conversation.id)
    payload = dict(state or {})
    agent_runtime._event(run, "run_result", runner_version=RUNNER_VERSION,
                         status=status, usage=totals,
                         artifact=payload.get("artifact_ref"),
                         candidates=payload.get("candidates") or [],
                         images=payload.get("images") or [],
                         actions=list((payload.get("pending_question") or {}).get("options")
                                      or []) if status == STATUS_WAITING_USER else [])
    agent_runtime._event(run, "run_finished", status=status, stop_reason=telemetry)

    run.status = status
    run.stop_reason = telemetry[:160]
    run.usage = {"runner_version": RUNNER_VERSION, **totals}
    run.finished_at = agent_runtime._now()
    _sync_task_state(db, conversation, status, ctx, payload)
    db.flush()


def _sync_task_state(db: Session, conversation: models.AgentConversation, status: str,
                     ctx: RuntimeContext, state: dict | None = None) -> None:
    """Keep the task-history row usable without clobbering 0.16 title/pin data."""
    state_row = db.get(models.AgentTaskState, conversation.id)
    data = dict(state_row.data or {}) if state_row else {}
    payload = dict(state or {})
    data["runner_version"] = RUNNER_VERSION
    data["artifact_count"] = db.query(models.AgentArtifact).filter(
        models.AgentArtifact.conversation_id == conversation.id).count()
    if payload.get("candidates"):
        data["candidates"] = payload["candidates"]
    elif isinstance(ctx.tool_ctx.last_search, dict) and ctx.tool_ctx.last_search.get("products"):
        data["candidates"] = [{"product_id": item.get("product_id"),
                               "model": item.get("model"), "reason": "", "facts": []}
                              for item in ctx.tool_ctx.last_search["products"][:3]]
    if payload.get("images"):
        data["images"] = payload["images"]
    if state_row is None:
        db.add(models.AgentTaskState(conversation_id=conversation.id, stage=status, data=data))
    else:
        state_row.stage = status
        state_row.data = data
    db.flush()


def delete_ledgers(db: Session, conversation_id: str) -> None:
    """Remove ledger rows when a conversation is deleted."""
    for table in (models.AgentRequestRecord, models.AgentModelAttempt, models.AgentEffect):
        db.query(table).filter(table.conversation_id == conversation_id).delete(
            synchronize_session=False)
    db.flush()


def state_view(db: Session, conversation: models.AgentConversation) -> dict:
    """Redacted operator view; never the raw graph state."""
    run = db.query(models.AgentRun).filter(
        models.AgentRun.conversation_id == conversation.id).order_by(
        models.AgentRun.created_at.desc()).first()
    if run is None:
        return {"conversation_id": conversation.id, "status": "none"}
    return {"conversation_id": conversation.id, "runner_version": RUNNER_VERSION,
            "run_id": run.id, "status": run.status, "stop_reason": run.stop_reason,
            "usage": run.usage or {}}