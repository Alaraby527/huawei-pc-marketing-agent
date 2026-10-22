"""Locking a conversation to a delivery surface has to be real, not a UI label."""
import pytest

from app import agent_runtime as ar
from app import db as db_layer, models
from app.runtime.context import build_context, surface_directive


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'surface.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    db_layer.reset_for_tests(url)
    db_layer.init_db()
    yield
    db_layer.reset_for_tests(url)


def _conversation(db, cid="c-lock"):
    row = models.AgentConversation(id=cid, workspace_id="default")
    db.add(row)
    db.flush()
    return row


def test_a_lock_is_stored_and_returned():
    session = db_layer.session_factory()()
    conversation = _conversation(session)
    result = ar.set_conversation_surface(session, conversation, "xiaohongshu")
    assert result["ok"] is True
    assert result["surface"] == "xiaohongshu"
    assert ar.get_conversation_surface(session, conversation)["surface"] == "xiaohongshu"
    session.close()


def test_an_ambiguous_platform_is_refused_rather_than_guessed():
    """`moments` 覆盖三个交付面，所以锁定它必须先问清是哪一个。"""
    session = db_layer.session_factory()()
    conversation = _conversation(session)
    result = ar.set_conversation_surface(session, conversation, "moments")
    assert result["ok"] is False
    assert result["error"] == "UNKNOWN_SURFACE"
        # 三个交付面共用 moments 平台：个人朋友圈、门店朋友圈、企业微信朋友圈
    assert sorted(result["ambiguous"]) == ["personal_moments", "store_moments", "work_account"]
    session.close()


def test_an_unknown_surface_is_refused():
    session = db_layer.session_factory()()
    conversation = _conversation(session)
    result = ar.set_conversation_surface(session, conversation, "douyin")
    assert result["ok"] is False
    assert "xiaohongshu" in result["available"]
    session.close()


def test_unlocking_clears_it():
    session = db_layer.session_factory()()
    conversation = _conversation(session)
    ar.set_conversation_surface(session, conversation, "xiaohongshu")
    ar.set_conversation_surface(session, conversation, "")
    assert ar.get_conversation_surface(session, conversation)["surface"] == ""
    session.close()


def test_the_lock_becomes_a_real_instruction_to_the_model():
    """A lock that only changes a label would not change what gets written."""
    session = db_layer.session_factory()()
    conversation = _conversation(session)
    ar.set_conversation_surface(session, conversation, "xiaohongshu")
    directive = surface_directive(session, conversation)
    assert "本次交付面已锁定" in directive
    assert "小红书图文" in directive
    assert "350-700" in directive, "长度建议必须进入指令"
    assert "编辑建议，不是平台规则" in directive
    session.close()


def test_the_directive_reaches_the_gateway_system_prompt():
    session = db_layer.session_factory()()
    conversation = _conversation(session)
    ar.set_conversation_surface(session, conversation, "store_poster")
    run = models.AgentRun(conversation_id=conversation.id, status="running")
    session.add(run)
    session.flush()
    ctx = build_context(session, conversation, run)
    assert "本次交付面已锁定" in ctx.gateway.system
    assert "门店海报" in ctx.gateway.system
    assert "主标题建议不超过 14 字" in ctx.gateway.system
    # The base instruction must survive: the directive is appended, not substituted.
    assert "你是华为 PC 营销内容助手" in ctx.gateway.system
    session.close()


def test_no_lock_means_no_directive():
    session = db_layer.session_factory()()
    conversation = _conversation(session)
    assert surface_directive(session, conversation) == ""
    run = models.AgentRun(conversation_id=conversation.id, status="running")
    session.add(run)
    session.flush()
    ctx = build_context(session, conversation, run)
    assert "本次交付面已锁定" not in ctx.gateway.system
    session.close()


def test_a_confirmed_surface_carries_its_rules_not_an_open_question():
    """`work_account` 曾经待确认，2026-10-22 已裁定为企业微信·朋友圈。

    所以指令里不该再有"确认前保持中性出口"这类待确认措辞，而该是确定的规格与合规要求。
    """
    session = db_layer.session_factory()()
    conversation = _conversation(session)
    ar.set_conversation_surface(session, conversation, "work_account")
    directive = surface_directive(session, conversation)
    assert "企业微信" in directive
    assert "确认前保持中性出口" not in directive, "已确认就不该再挂待确认措辞"
    assert "文字不超过 8 行" in directive, "包 1 给的行数约束要进指令"
    assert "广告法" in directive and "冒充私人朋友圈" in directive, "合规要求要进指令"
    session.close()


def test_the_lock_survives_a_second_read_like_a_later_turn_would():
    session = db_layer.session_factory()()
    conversation = _conversation(session)
    ar.set_conversation_surface(session, conversation, "personal_moments")
    session.commit()
    again = session.get(models.AgentConversation, conversation.id)
    assert ar.get_conversation_surface(session, again)["label"] == "个人朋友圈"
    session.close()
