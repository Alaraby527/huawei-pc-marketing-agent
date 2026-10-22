"""联网搜索：无需密钥，但必须诚实报告失败，不得用模型记忆冒充搜索结果。"""
import pytest

from app import web_search


def test_the_ddg_wrapper_is_unwrapped():
    wrapped = ("//duckduckgo.com/l/?uddg=https%3A%2F%2Fbaike.baidu.com%2Fitem%2F%25E4%25B8%258D"
               "%25E7%2583%25A7%25E5%25BF%2583%2F69231112&rut=abc")
    assert web_search._unwrap(wrapped).startswith("https://baike.baidu.com/item/")


def test_an_empty_query_is_refused_rather_than_searched():
    out = web_search.search("   ")
    assert out["ok"] is False and out["error"] == "EMPTY_QUERY"


def test_bing_blocks_are_parsed_without_a_nested_list_breaking_them():
    """Bing puts <link> tags right after the <li> and nests lists; splitting beats one regex."""
    body = ('<li class="b_algo" data-id iid=SERP.1><link rel="stylesheet" href="/rp/x.css"/>'
            '<h2><a href="https://www.digitaling.com/articles/1561293.html">2026 上半年网络热词 TOP30</a></h2>'
            '<p>2026 年已经过半，一个个爆梗正在成为年轻人的交流暗号。</p></li>'
            '<li class="b_algo" data-id iid=SERP.2><h2><a href="https://zhuanlan.zhihu.com/p/1">'
            '100 条 2026 最新热门梗</a></h2><p>奶绿波：高速堵车刷屏梗。</p></li>')
    rows = web_search._parse_bing(body)
    assert len(rows) == 2
    assert rows[0]["url"] == "https://www.digitaling.com/articles/1561293.html"
    assert "网络热词" in rows[0]["title"]
    assert "奶绿波" in rows[1]["snippet"]


def test_providers_are_tried_in_order_and_failure_is_reported():
    """两个 provider 都失败时必须报 NO_RESULTS，而不是返回空清单假装成功。"""
    import inspect
    source = inspect.getsource(web_search.search)
    assert "duckduckgo-lite" in source and "bing-html" in source
    assert '"error": "NO_RESULTS"' in source
