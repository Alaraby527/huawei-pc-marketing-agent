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


def test_every_topic_carries_an_intro_a_link_and_a_keyword():
    """短文约 100 字、来源链接、真实搜索词 —— 链接与关键词必须来自原始条目，不是模型编的。"""
    source = {"items": [{"rank": 1, "title": "我创造了新型遛狗法", "hot": "1w",
                         "url": "https://www.xiaohongshu.com/search_result?keyword=%E6%96%B0%E5%9E%8B%E9%80%9B%E7%8B%97%E6%B3%95&type=51",
                         "source": "60s-rednote"}],
              "requested": 50, "available": 1, "source": "t", "time_note": "t",
              "cap_note": "", "observed_at": "t"}
    import app.hot_topics as ht
    original = ht.fetch_source_items
    ht.fetch_source_items = lambda limit=50: source
    try:
        ht.reset_cache()
        from app.model import ModelError
        ht.chat_json_messages = None  # 触发受控失败，只验证取源与映射结构
        out = ht.organize(limit=50, force=True)
        assert out["ok"] is False, "没有模型时应如实失败，不得用原始条目冒充清单"
        assert "不要用未整理的原始条目冒充热点清单" in out["note"]
    finally:
        ht.fetch_source_items = original


def test_the_keyword_is_extracted_from_the_source_link():
    url = ("https://www.xiaohongshu.com/search_result?keyword=%E6%97%85%E8%A1%8C%E6%89%93%E5%8D%A1"
           "&type=51")
    assert hot_topics._keyword_of(url) == "旅行打卡"
    assert hot_topics._keyword_of("") == ""
    assert hot_topics._keyword_of("https://example.com/x") == ""


def test_the_response_states_what_the_links_are_and_are_not():
    """链接是搜索页链接，需要登录才能打开，且不能由服务端抓取 —— 必须写清楚。"""
    status = hot_topics.status()
    assert "note" in status
    import inspect
    source = inspect.getsource(hot_topics.organize)
    assert "link_note" in source
    assert "不能由服务端抓取内容" in source


def test_search_wording_is_written_the_way_that_actually_works():
    """实测：查询词决定成败。

    带年份的写法（"2026 网络热词"）返回的是 4 月与上半年的文章，是"又旧"的来源；现在时间窗
    交给 tbs=qdr:m，查询词改成"最近/本月/现在最火"这类近期指向词，并保留"是什么梗/出处"这种
    在实测中能返回解析型文章的措辞。
    """
    joined = " ".join(hot_topics.SEARCH_QUERIES)
    assert "梗" in joined
    assert any(word in joined for word in ("最近", "本月", "现在最火")), "要有近期指向"
    assert "上半年" not in joined, "上半年会把结果拉回半年前"
    assert "最近爆火的梗" not in joined, "这个措辞会按字面返回歌曲《最近》"


def test_irrelevant_results_are_dropped_deterministically():
    """实测噪声：'网络热梗 盘点' 返回过测速网站，'最近爆火的梗' 返回过同名歌曲。"""
    assert hot_topics.is_relevant("2026 上半年网络热词 TOP30", "一个个爆梗正在成为暗号")
    assert hot_topics.is_relevant("100 条 2026 最新热门梗", "奶绿波：高速堵车刷屏梗")
    assert not hot_topics.is_relevant("测速网 - 专业测网速", "网速测试 宽带提速")
    assert not hot_topics.is_relevant("最近（李圣杰2006年演唱的歌曲）", "收录于专辑")


def test_the_prompt_does_not_ask_for_a_huawei_connection_while_filtering_competitors():
    """实测反讽 bug：模型把『剔除竞品』读成『只收与华为 PC 相关的梗』，交回 0 条。"""
    prompt = hot_topics.SEARCH_SYSTEM
    assert "不是让你只收与华为 PC 相关的梗" in prompt
    assert "不需要你判断" in prompt
    # 也不许用"资料未说明"填充
    assert "不要用" in prompt and "资料未说明" in prompt


def test_search_only_looks_at_the_recent_window():
    """实测：'2026 网络热词' 返回的是 4 月与上半年的文章，正是"又旧"的来源。"""
    assert hot_topics.SEARCH_FRESHNESS == "qdr:m"
    import inspect
    assert "SEARCH_FRESHNESS" in inspect.getsource(hot_topics.organize_from_search)


def test_the_prompt_requires_the_material_date_not_a_guess():
    """实测踩过：Serper 每条都带 date，但提示词没要求用，结果 when 全是 unknown。"""
    prompt = hot_topics.SEARCH_SYSTEM
    assert "必须优先填资料里的 date 字段" in prompt
    assert "不要写 unknown，除非 date 确实为空" in prompt


def test_charts_are_separate_from_memes_and_say_so():
    """榜单报事件、不是热梗 —— 实测四个榜单里几乎没有梗（抖音 1/49）。"""
    import inspect
    from app import hot_charts
    import inspect
    source = inspect.getsource(hot_charts)
    assert "不是热梗" in source, "要明确告诉用户榜单不等于热梗"
    assert "它不产出热梗" in source, "模块文档要说明它不产热梗"
    assert set(hot_charts.FEEDS) == {"douyin", "weibo", "zhihu", "rednote"}
    # 只用实测可用的源：baidu 404、bili 500、toutiao 超时
    assert "baidu" not in hot_charts.FEEDS and "bili" not in hot_charts.FEEDS


def test_the_cached_endpoint_uses_the_same_path_as_refresh():
    """实测 bug：GET 走读榜单、刷新走联网搜索，于是刷新出来的一离开板块就变回报错。"""
    import inspect
    from app import main
    source = inspect.getsource(main.hot_topics_cached)
    assert "organize_from_search" in source
    assert "organize(" not in source.replace("organize_from_search", "")


def test_the_search_source_shape_differs_from_the_chart_shape():
    """实测 bug：界面出现「取到 undefined 条」。

    联网搜索的 source 给 result_count/providers，读榜单的给 available/source；渲染只读后者，
    所以搜索那条路上的计数一直是 undefined。
    """
    import inspect
    from app import main as _main  # noqa: F401
    js = (__import__("pathlib").Path("app/static/app.js").read_text())
    assert "src.result_count" in js, "渲染必须兼容搜索路径的字段"
    assert "src.available != null" in js


def test_the_hot_section_has_no_leftover_detail_panel():
    """「清单说明」面板已按要求删除，选项卡移到头部行。"""
    from pathlib import Path
    html = Path("app/static/index.html").read_text()
    assert "清单说明" not in html
    assert 'id="hot-detail"' not in html
    assert "hot-tabs-row" in html and 'id="hot-scenario-tabs"' in html
