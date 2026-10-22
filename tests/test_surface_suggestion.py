"""Surface suggestions: propose, confirm, then switch - and never second-guess an explicit choice.

The user's rule has two halves: when the intent is unclear the assistant may work out the right
delivery surface and offer to switch, but when the user has already been explicit the assistant
must not switch on its own. A lock *is* the explicit choice, so a suggestion must find nothing to
do once one exists.
"""
import asyncio

import pytest

from app import agent_runtime as ar
from app import db as db_layer, models


def _run(coro):
    return asyncio.run(coro)


class _Ctx:
    """Just enough of a ToolContext. The run is real because the tool records the suggestion
    on it - a run that pauses to ask a question never reaches finalize, so the event is how the
    suggestion survives."""

    def __init__(self, db, conversation):
        self.db = db
        self.conversation = conversation
        self.surface_suggestion = None
        self.run = models.AgentRun(conversation_id=conversation.id, status="running", events=[])


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'suggest.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    db_layer.reset_for_tests(url)
    db_layer.init_db()
    yield
    db_layer.reset_for_tests(url)


def _conversation(db, cid="c-suggest"):
    row = models.AgentConversation(id=cid, workspace_id="default")
    db.add(row)
    db.flush()
    return row


def test_a_surface_without_a_reason_is_still_recorded():
    session = db_layer.session_factory()()
    ctx = _Ctx(session, _conversation(session))
    result = ar._tool_suggest_surface(ctx, {"surface": "store_poster", "reason": "海报需求"})
    assert result["ok"] is True
    assert result["requires_confirmation"] is True
    assert ctx.surface_suggestion["surface"] == "store_poster"
    assert "等用户在界面上确认" in result["message"]
    session.close()


def test_it_requires_a_surface():
    session = db_layer.session_factory()()
    ctx = _Ctx(session, _conversation(session))
    result = ar._tool_suggest_surface(ctx, {"surface": "", "reason": "x"})
    assert result["ok"] is False and result["error"] == "SURFACE_REQUIRED"
    session.close()


def test_an_ambiguous_surface_is_refused():
    """`moments` 覆盖三个交付面，助手必须显式选一个。"""
    session = db_layer.session_factory()()
    ctx = _Ctx(session, _conversation(session))
    result = ar._tool_suggest_surface(ctx, {"surface": "moments", "reason": "朋友圈"})
    assert result["ok"] is False
    assert result["error"] == "UNKNOWN_SURFACE"
        # 三个交付面共用 moments 平台：个人朋友圈、门店朋友圈、企业微信朋友圈
    assert sorted(result["ambiguous"]) == ["personal_moments", "store_moments", "work_account"]
    assert ctx.surface_suggestion is None
    session.close()


def test_an_unknown_surface_is_refused():
    session = db_layer.session_factory()()
    ctx = _Ctx(session, _conversation(session))
    result = ar._tool_suggest_surface(ctx, {"surface": "douyin", "reason": "x"})
    assert result["ok"] is False
    assert "xiaohongshu" in result["available"]
    session.close()


def test_an_explicit_choice_is_never_second_guessed():
    """A lock is the user being explicit, so nothing is suggested and nothing changes."""
    session = db_layer.session_factory()()
    conversation = _conversation(session)
    ar.set_conversation_surface(session, conversation, "xiaohongshu")
    ctx = _Ctx(session, conversation)
    result = ar._tool_suggest_surface(ctx, {"surface": "store_poster", "reason": "看起来像海报"})
    assert result["ok"] is True
    assert result["skipped"] == "already_locked"
    assert result["locked"] == "xiaohongshu"
    assert ctx.surface_suggestion is None, "已明确指定时不得产生建议"
    assert "不重复建议" in result["message"]
    session.close()


def test_a_confirmed_surface_suggests_without_an_open_question():
    """work_account 已确认为企业微信·朋友圈，建议里不再带待确认问题。"""
    session = db_layer.session_factory()()
    ctx = _Ctx(session, _conversation(session))
    result = ar._tool_suggest_surface(ctx, {"surface": "work_account", "reason": "工作号发一条"})
    assert result["ok"] is True
    suggestion = ctx.surface_suggestion
    assert suggestion["surface"] == "work_account"
    assert suggestion["unconfirmed"] is False
    assert suggestion["open_question"] == ""
    assert suggestion["platform"] == "moments"
    session.close()


def test_the_suggestion_reaches_the_run_result():
    """The UI can only ask the user if the suggestion travels with the payload."""
    from app.runtime import graph  # noqa: F401 - imported to prove the wiring exists
    import inspect
    source = inspect.getsource(graph)
    assert 'candidate["surface_suggestion"] = suggestion' in source

    from app.runtime import runner
    runner_source = inspect.getsource(runner)
    assert "surface_suggestion=_surface_suggestion(run, candidate)" in runner_source


def test_the_tool_is_registered_for_the_model():
    spec = next((item for item in ar.TOOL_SPECS if item["name"] == "suggest_surface"), None)
    assert spec is not None
    assert spec["parameters"]["required"] == ["surface", "reason"]
    assert "已明确指定就不要调用" in spec["description"]
    surfaces = spec["parameters"]["properties"]["surface"]["enum"]
    assert "store_moments" in surfaces and "work_account" in surfaces


def test_suggesting_does_not_change_the_lock_by_itself():
    """Suggestion and switching are separate steps on purpose."""
    session = db_layer.session_factory()()
    conversation = _conversation(session)
    ctx = _Ctx(session, conversation)
    ar._tool_suggest_surface(ctx, {"surface": "wechat_article", "reason": "长文"})
    assert ar.get_conversation_surface(session, conversation)["surface"] == ""
    session.close()

def test_the_suggestion_survives_a_run_that_never_reaches_finalize():
    """A run ending in waiting_user never runs finalize, and that is the common case.

    Found live: the tool was called, the suggestion was recorded, and the payload had nothing
    because the pause happened before finalize.
    """
    import inspect
    from app.runtime import runner
    source = inspect.getsource(runner)
    assert "_surface_suggestion(run, candidate)" in source
    assert 'event.get("type") == "surface_suggestion"' in source

    run = models.AgentRun(conversation_id="c1", status="waiting_user",
                          events=[{"type": "surface_suggestion", "surface": "store_poster",
                                   "label": "门店海报", "at": "2026-10-22T00:00:00"},
                                  {"type": "run_result", "status": "waiting_user"}])
    picked = runner._surface_suggestion(run, {})
    assert picked["surface"] == "store_poster"
    assert "at" not in picked and "type" not in picked


def test_the_candidate_wins_when_both_exist():
    from app.runtime import runner
    run = models.AgentRun(conversation_id="c1", status="completed",
                          events=[{"type": "surface_suggestion", "surface": "article"}])
    candidate = {"surface_suggestion": {"surface": "store_poster"}}
    assert runner._surface_suggestion(run, candidate)["surface"] == "store_poster"
    assert runner._surface_suggestion(models.AgentRun(conversation_id="c1", events=[]), {}) is None
