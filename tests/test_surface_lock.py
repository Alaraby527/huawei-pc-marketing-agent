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
    """`moments` covers two surfaces, so locking to it must ask which one."""
    session = db_layer.session_factory()()
    conversation = _conversation(session)
    result = ar.set_conversation_surface(session, conversation, "moments")
    assert result["ok"] is False
    assert result["error"] == "UNKNOWN_SURFACE"
    assert sorted(result["ambiguous"]) == ["personal_moments", "store_moments"]
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


def test_an_unconfirmed_surface_carries_its_open_question_into_the_instruction():
    """`work_account` has no confirmed platform, so the model must be told not to assume one."""
    session = db_layer.session_factory()()
    conversation = _conversation(session)
    ar.set_conversation_surface(session, conversation, "work_account")
    directive = surface_directive(session, conversation)
    assert "工作号" in directive
    # The open question travels verbatim, which is more precise than a generic warning.
    assert "确认前保持中性出口" in directive
    assert "不得按公众号规格生成" in directive
    session.close()


def test_the_lock_survives_a_second_read_like_a_later_turn_would():
    session = db_layer.session_factory()()
    conversation = _conversation(session)
    ar.set_conversation_surface(session, conversation, "personal_moments")
    session.commit()
    again = session.get(models.AgentConversation, conversation.id)
    assert ar.get_conversation_surface(session, again)["label"] == "个人朋友圈"
    session.close()


def test_surfaces_are_flat_five_tabs_above_the_composer():
    """交付面从标题栏挪到输入框上方，并平铺成 5 个。

    平铺的收益是去掉一层交互状态：原来「朋友圈」需要再弹出门店/个人二选一，既多一步，
    也让 currentSurface() 必须处理 moments 歧义。拆开之后选项卡 id 就是交付面 id。
    """
    from pathlib import Path
    html = Path("app/static/index.html").read_text()
    js = Path("app/static/app.js").read_text()

    for surface in ("xiaohongshu", "wechat_article", "personal_moments",
                    "store_moments", "store_poster"):
        assert f'data-surface="{surface}"' in html, surface
    assert html.count('data-surface="') == 5, "应为 5 个平铺选项卡"
    assert "moments-split" not in html, "二选一的 split 应已移除"
    assert "moments-split" not in js
    assert "data-moments" not in js

    # 位置：在输入框上方，不再占标题栏那一行
    assert html.index('class="surface-bar"') > html.index('class="chat-log"')
    assert html.index('class="surface-bar"') < html.index('id="composer"')
    assert 'class="surface-bar"' not in html[:html.index('class="chat-log"')], \
        "标题栏里不应再有交付面"


def test_surface_lock_highlights_the_exact_surface_not_a_moments_group():
    """踩过：高亮时把 personal/store 都映射回 motifs 组的 'moments'，会高亮错选项卡。"""
    from pathlib import Path
    js = Path("app/static/app.js").read_text()
    assert "item.dataset.surface === info.surface" in js
    assert "'moments' : info.surface" not in js
