"""热点清单：确定性品牌过滤 + 模型整理，且不排序不打分。"""
import pytest

from app import hot_topics


def test_other_brands_are_filtered_out_deterministically():
    """这是华为 PC 的账号，靠别的品牌才成立的话题对我们没用 —— 不靠模型自觉。"""
    for title in ("iPhone 17 上手体验", "小米新笔记本发布", "联想 ThinkPad 评测",
                  "MacBook Air 降价", "荣耀 MagicBook 对比"):
        assert hot_topics.hits_other_brand(title), title
    for title in ("旅行拍照万能姿势", "家庭旅行像打副本", "拼豆作画"):
        assert hot_topics.hits_other_brand(title) == "", title


def test_split_reports_what_it_dropped_instead_of_silently_removing():
    kept, dropped = hot_topics.split_by_brand([
        {"title": "旅行拍照姿势"}, {"title": "iPhone 新机开箱"}, {"title": "拼豆作画"}])
    assert [row["title"] for row in kept] == ["旅行拍照姿势", "拼豆作画"]
    assert len(dropped) == 1
    assert dropped[0]["excluded_brand"] == "iphone"
    assert dropped[0]["title"] == "iPhone 新机开箱"


def test_the_organize_prompt_carries_the_users_editorial_direction():
    prompt = hot_topics.ORGANIZE_SYSTEM
    assert "往好玩的方向走" in prompt
    assert "新闻事件类不要" in prompt
    assert "涉及其他数码、手机、电脑品牌的条目不要" in prompt
    # 不排序不打分是明确要求
    assert "不排序、不打分" in prompt
    # 不写借势：上一版要求每条给借势理由，结果模型要么硬凑要么全盘否定
    assert "不要写" in prompt and "借势理由" in prompt


def test_the_rules_are_machine_readable_too():
    status = hot_topics.status()
    assert status["rules"]["prefer"] == "好玩、有梗、有共鸣"
    assert status["rules"]["skip"] == "新闻事件类"
    assert status["rules"]["never"] == "涉及其他数码/手机/电脑品牌"
    assert status["source_cap"] == 20, "该公开源上限就是 20 条，不得虚报 50"


def test_the_source_reports_its_own_limits():
    source = hot_topics.fetch_source_items(50)
    assert source["requested"] == 50
    assert "time_note" in source and "不是平台的更新时间" in source["time_note"]
    if source["available"] < 50:
        assert source["cap_note"], "不足 50 条时必须说明是源的上限"


def test_cache_prevents_paying_twice_for_one_click(monkeypatch):
    hot_topics.reset_cache()
    calls = {"n": 0}

    def fake_organize(**kwargs):
        calls["n"] += 1
        return {"ok": True, "cached": False, "topic_count": 0, "topics": [],
                "source": {"available": 1}, "excluded_by_brand": []}

    monkeypatch.setattr(hot_topics, "fetch_source_items",
                        lambda limit=50: {"items": [{"title": "x"}], "available": 1,
                                          "requested": limit, "source": "t", "time_note": "t",
                                          "cap_note": "", "observed_at": "t"})
    monkeypatch.setattr(hot_topics, "chat_json_messages", None, raising=False)
    # 直接验证缓存分支：第一次写入缓存，第二次应命中
    hot_topics._CACHE["50"] = (__import__("time").monotonic(), {"ok": True, "cached": False})
    second = hot_topics.organize(limit=50)
    assert second["cached"] is True
    hot_topics.reset_cache()
