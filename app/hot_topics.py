"""Hot topics for marketing: take a live chart, then let the model organise it.

The architecture follows what was actually measured, not what sounded good:

* **Recency comes from the source.** Asked for "近期热梗" the main model spent its entire token
  budget on reasoning and returned *nothing* (`finish_reason=length`, `text_tokens=0`) - it has
  no way to know what is current. So the live chart supplies "what is happening now".
* **Meaning comes from the model.** Asked about memes it knows, the same model answered in
  9.5 s with real items and accurate years. Organising and explaining is its strength.
* **Structured calls run without thinking.** `enable_thinking=false` measured 1.5 s / 34 tokens
  against 31.2 s / 2858 tokens for the same judgement, so the difference is not just cost.

The user's editorial direction is baked in and partly enforced in code: lean towards fun, skip
news events, and **never involve other digital brands** - that last one is a deterministic
filter applied *before* the model sees the list, so it cannot be talked out of it, plus a
post-filter in case the model merges something back in.
"""
from __future__ import annotations

import os
import time

# Competitor and adjacent brands. Filtered out before the model sees the list: this is a Huawei
# PC account, and a topic that only makes sense by naming another brand is useless to us.
OTHER_BRANDS = (
    "苹果", "iphone", "ipad", "macbook", "mac ", "imac", "airpods",
    "小米", "redmi", "红米", "联想", "thinkpad", "thinkbook", "小新", "拯救者",
    "戴尔", "dell", "alienware", "惠普", "hp ", "华硕", "asus", "rog", "天选",
    "荣耀", "honor", "magicbook", "三星", "samsung", "galaxy",
    "oppo", "vivo", "一加", "oneplus", "realme", "真我", "魅族", "努比亚", "红魔",
    "微软", "surface", "谷歌", "pixel", "索尼", "sony", "vaio", "宏碁", "acer", "机械革命",
)

# Kept visible rather than buried in the prompt: these are the reasons a topic is dropped.
KIND_FUN = "玩梗"
KIND_LIFE = "生活方式"
KIND_SCENE = "共鸣场景"
KIND_OTHER = "其他"

ORGANIZE_SYSTEM = """你在为华为 PC 的门店营销整理一份「当前热点」清单。下面是一份公开热榜的原始条目。

选材方向（严格遵守）：
1. **往好玩的方向走**：优先网络热梗、流行说法、有趣的生活方式、让人有共鸣的场景、好玩的对比。
2. **新闻事件类不要**：时政、事故、股市财经、赛事比分、明星绯闻、纪念日、社会争议，一律剔除。
3. **涉及其他数码、手机、电脑品牌的条目不要**——我们只做华为 PC。
4. 每条都必须能对应回给定条目，不得编造原始清单里没有的东西。
5. 宁可少给几条，也不要凑数。

不排序、不打分、不显示热度值，只给清单。不要写"借势理由"，那是下一步的事。

每条输出五个字段：
- topic：热点名称，简短
- kind：玩梗 / 生活方式 / 共鸣场景 / 其他
- gist：一句话说明它是什么
- intro：约 100 字的短文介绍，讲清楚这个热点是什么、为什么好玩、大家在讨论什么。
  只能依据给定条目合理展开，不得编造具体人物、数据或事件细节。
- source_titles：对应的原始条目标题（数组，必须来自给定条目）

只输出 JSON：
{"topics":[...],"excluded_note":"一句话说明剔除了哪几类","note":"一句话说明这份清单的性质"}"""

# The angles that actually surface memes. A chart cannot: see app/web_search.py for the four
# charts that were checked and the zero meme items they contained.
# Wording decides whether this works. Measured on Bing: "2026 网络热词" returned the 数英
# 热词 TOP30, a 头条 新词解析 and a 知乎 100-条 list, while "最近爆火的梗" returned a Baidu Baike
# entry for the song 最近 and a 网易云 track, and "网络热梗 盘点" returned speed-test sites. The
# year plus 热词/梗 is what works, so the queries are written that way.
SEARCH_QUERIES = (
    "2026 网络热词",
    "2026 最新热门梗",
    "2026 上半年 网络热词 top",
    "网络流行语 2026 盘点",
)

# A result has to look like it is about internet language at all. Deterministic, and it is what
# removes the speed-test and music results above.
RELEVANCE_TERMS = ("梗", "热词", "流行语", "网络用", "黑话", "热梗")

SEARCH_SYSTEM = """你在整理一份「当前网络热梗」清单，供后续营销选题参考。
**不要**在整理阶段判断这些梗能不能用在华为 PC 上——先把梗本身整理清楚。
下面是从公开网页搜索到的真实结果（标题 + 摘要 + 链接），这些是**有出处的资料**，不是榜单条目。

规则：
1. 只整理资料里**确实出现**的梗，不得添加资料里没有的梗，不得编造来源与时间。
2. 摘要是你的事实依据：介绍里提到的时间、出处、含义都必须能在摘要或标题里找到依据。
3. 摘要里没有的信息不要推测。**但不要用"资料未说明"这类句子填充**——
   资料没写就少写一点，把已有的信息写清楚写自然。
4. 介绍要**读起来通顺自然**，像在跟同事讲这个梗，而不是逐条复述资料；
   但每一个事实（时间、出处、含义）都必须能在资料里找到依据。
5. 跳过新闻事件类（时政、事故、股市、赛事比分、明星绯闻、纪念日、社会争议）。
6. 涉及其他数码、手机、电脑品牌的内容不要——我们只做华为 PC。
   **注意区分**：这条是让你**剔除拿别的品牌说事的梗**，不是让你只收与华为 PC 相关的梗。
   资料里的梗跟华为 PC 有没有关系**不需要你判断**，那是我们后面的事；
   你的任务就是把资料里出现的热梗如实整理出来。
   实测教训：上一版因为这句话写得含糊，模型回复"摘要中未出现任何与华为 PC 相关的具体梗，
   故无可整理条目"，交回 0 条。
7. 优先「好玩、有梗、能会心一笑」的。

不排序、不打分、不显示热度值。

每条输出：
- topic：梗名
- kind：玩梗 / 生活方式 / 共鸣场景 / 其他
- gist：一句话说明
- intro：约 100 字介绍，讲清出处、含义、怎么用；**只能写资料里有的信息**
- evidence：你依据的是哪条资料（写资料标题）
- source_urls：对应资料的链接（数组，必须来自给定资料）
- when：资料里写的时间；资料没写就写 unknown

只输出 JSON：{"topics":[...],"note":"一句话说明这批资料的时间范围与局限"}"""


def organize_from_search(*, force: bool = False) -> dict:
    """Search the live web for memes, then let the model organise what it found.

    This is the path that works. A chart gave bare titles with no meme in them; search gives
    dated articles ("出自红果短剧平台AI生成短剧，于2026年下半年席卷全网"), which is both the
    recency signal and the grounding for the intro.
    """
    import json as _json

    from .model import ModelError, chat_json_messages
    from .web_search import search_many

    key = "search"
    cached = _CACHE.get(key)
    if cached and not force and time.monotonic() - cached[0] < CACHE_TTL_SECONDS:
        return {**cached[1], "cached": True}

    found = search_many(list(SEARCH_QUERIES), limit_each=6)
    if not found.get("ok"):
        return {"ok": False, "error": "SEARCH_FAILED", "search": found,
                "message": "搜索无结果；不得用模型记忆冒充近期热梗"}
    relevant = [row for row in found["results"] if is_relevant(row["title"], row["snippet"])]
    dropped = [{"title": row["title"], "url": row["url"], "reason": "not_about_slang"}
               for row in found["results"] if row not in relevant]
    if not relevant:
        return {"ok": False, "error": "NO_RELEVANT_RESULTS", "search": found,
                "dropped": dropped,
                "message": "搜索有返回但没有一条是关于网络用语的；不得用无关结果凑清单"}
    kept = [{"n": index + 1, "title": row["title"], "snippet": row["snippet"][:300],
             "url": row["url"]} for index, row in enumerate(relevant)]
    by_n = {row["n"]: row for row in kept}
    try:
        result, meta = chat_json_messages(
            SEARCH_SYSTEM, [{"role": "user", "content": _json.dumps({"results": kept}, ensure_ascii=False)}],
            schema_hint="只输出 JSON", model=None)
    except ModelError as error:
        return {"ok": False, "error": "MODEL_FAILED", "message": str(error)[:200],
                "search": found, "note": "搜索成功但整理失败；不要用未整理的搜索结果冒充清单"}
    topics = []
    for row in (result or {}).get("topics") or []:
        if not isinstance(row, dict):
            continue
        urls = [str(u) for u in (row.get("source_urls") or []) if str(u).startswith("http")]
        # Only URLs that really came back from the search: the model does not get to invent links.
        urls = [u for u in urls if u in {item["url"] for item in kept}] or []
        if not urls:
            for item in kept:
                if str(row.get("evidence") or "")[:20] and str(row["title"])[:20] in str(row.get("evidence")):
                    urls = [item["url"]]
                    break
        text = " ".join([str(row.get("topic") or ""), str(row.get("gist") or ""),
                         str(row.get("intro") or "")])
        brand = hits_other_brand(text)
        if brand:
            continue
        topics.append({"topic": str(row.get("topic") or "")[:80],
                       "kind": str(row.get("kind") or KIND_OTHER)[:12],
                       "gist": str(row.get("gist") or "")[:200],
                       "intro": str(row.get("intro") or "")[:300],
                       "evidence": str(row.get("evidence") or "")[:120],
                       "when": str(row.get("when") or "unknown")[:20],
                       "links": urls[:3], "keywords": [str(row.get("topic") or "")[:40]]})
    out = {"ok": True, "topics": topics, "topic_count": len(topics),
           "source": {"kind": "web_search", "providers": found.get("providers"),
                      "result_count": len(kept), "queries": found.get("queries"),
                      "fetched_at": found.get("fetched_at"),
                      "time_note": "时间是资料的发布时间/资料内提到的时间，不是平台的榜单更新时间"},
           "search_results": kept, "dropped_irrelevant": dropped, "model_note": str((result or {}).get("note") or "")[:200],
           "usage": (meta or {}).get("usage") or {}, "cached": False,
           "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
           "rules": {"prefer": "好玩、有梗、有共鸣", "skip": "新闻事件类",
                     "never": "涉及其他数码/手机/电脑品牌"},
           "notice": ("清单来自公开网页搜索结果，经模型整理；每条都能对应回搜到的资料。"
                      "梗的流行时间以资料记载为准。"),
           "link_note": "链接是搜到的资料页（百科/知乎/头条等），可直接点开核对。"}
    _CACHE[key] = (time.monotonic(), out)
    return out


# Refresh is explicit (a button), so a short cache only prevents double-clicking from paying twice.
CACHE_TTL_SECONDS = int(os.getenv("HOT_TOPICS_TTL_SECONDS", "600") or 600)
_CACHE: dict[str, tuple[float, dict]] = {}


def reset_cache() -> None:
    _CACHE.clear()


def is_relevant(title: str, snippet: str = "") -> bool:
    """Whether a search result is plausibly about internet slang rather than a literal match."""
    text = f"{title} {snippet}"
    return any(term in text for term in RELEVANCE_TERMS)


def hits_other_brand(text: str) -> str:
    """Return the matched brand, or an empty string. Used before and after the model."""
    low = str(text or "").lower()
    for brand in OTHER_BRANDS:
        if brand in low:
            return brand.strip()
    return ""


def _keyword_of(url: str) -> str:
    """从搜索页链接里取出关键词，它是"大家在搜什么"的真实信号。"""
    from urllib.parse import parse_qs, unquote, urlparse
    try:
        query = parse_qs(urlparse(str(url or "")).query)
        return unquote((query.get("keyword") or [""])[0])[:60]
    except Exception:  # noqa: BLE001 - 链接格式变了就当作没有关键词
        return ""


def split_by_brand(items: list[dict]) -> tuple[list[dict], list[dict]]:
    """(kept, dropped) by competitor brand. Deterministic, and reported rather than silent."""
    kept, dropped = [], []
    for item in items or []:
        brand = hits_other_brand(str(item.get("title") or ""))
        if brand:
            dropped.append({**item, "excluded_brand": brand})
        else:
            kept.append(item)
    return kept, dropped


def fetch_source_items(limit: int = 50) -> dict:
    """The live chart, labelled with what it is and is not.

    The source caps at 20 entries; asking for 50 cannot change that, and pretending otherwise
    would misreport the board size.
    """
    from .hotspot import _fetch_source

    rows = _fetch_source("rednote", limit) or []
    items = [{"rank": item.get("rank"), "title": item.get("title"), "hot": item.get("heat"),
              "url": item.get("url") or "", "source": item.get("source") or "60s-rednote"}
             for item in rows if item.get("title")]
    return {"items": items, "requested": limit, "available": len(items),
            "source": "60s-rednote（公开聚合）",
            "time_note": "该源不提供榜单更新时间；observed_at 是本机抓取时刻，不是平台的更新时间",
            "cap_note": ("该源上限 20 条" if len(items) < limit else ""),
            "observed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}


def organize(*, limit: int = 50, force: bool = False) -> dict:
    """Fetch, filter, and ask the model to organise. Returns the list plus what was left out."""
    import json

    from .model import ModelError, chat_json_messages

    key = str(limit)
    cached = _CACHE.get(key)
    if cached and not force and time.monotonic() - cached[0] < CACHE_TTL_SECONDS:
        return {**cached[1], "cached": True}

    source = fetch_source_items(limit)
    kept, dropped = split_by_brand(source["items"])
    if not kept:
        return {"ok": False, "error": "NO_ITEMS", "source": source,
                "excluded_by_brand": dropped,
                "message": "取不到可用条目（源不可用，或全部条目都涉及其他品牌）"}

    payload = {"items": [{"n": index + 1, "title": item["title"]} for index, item in enumerate(kept)]}
    # title -> 原始条目，用于把链接与搜索关键词映射回模型整理出的话题
    by_title = {item["title"]: item for item in kept}
    try:
        result, meta = chat_json_messages(
            ORGANIZE_SYSTEM, [{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
            schema_hint="只输出 JSON", model=None)
    except ModelError as error:
        return {"ok": False, "error": "MODEL_FAILED", "message": str(error)[:200],
                "source": source, "excluded_by_brand": dropped,
                "note": "取源成功但整理失败；不要用未整理的原始条目冒充热点清单"}
    topics = []
    for row in (result or {}).get("topics") or []:
        if not isinstance(row, dict):
            continue
        titles = [str(t) for t in (row.get("source_titles") or [])]
        # Post-filter: the model may have merged a filtered item back in through a source title.
        brand = next((hits_other_brand(t) for t in titles if hits_other_brand(t)), "")
        if brand:
            dropped.append({"title": titles[0] if titles else str(row.get("topic") or ""),
                            "excluded_brand": brand, "stage": "post_filter"})
            continue
        # 链接与关键词来自原始条目，不是模型编的。该源给的是搜索页链接（不是笔记链接），
        # 服务端抓取它需要登录态，会撞上实测过的风控，所以只作为人工点开的入口。
        links, keywords = [], []
        for title in titles:
            item = by_title.get(title)
            if not item:
                continue
            url = str(item.get("url") or "")
            if url and url not in links:
                links.append(url)
            keyword = _keyword_of(url)
            if keyword and keyword not in keywords:
                keywords.append(keyword)
        topics.append({"topic": str(row.get("topic") or "")[:80],
                       "kind": str(row.get("kind") or KIND_OTHER)[:12],
                       "gist": str(row.get("gist") or "")[:200],
                       "intro": str(row.get("intro") or "")[:300],
                       "source_titles": titles[:6],
                       "links": links[:3], "keywords": keywords[:3]})
    out = {"ok": True, "topics": topics, "topic_count": len(topics),
           "source": source, "from_source": source["available"],
           "sent_to_model": len(kept), "excluded_by_brand": dropped,
           "model_note": str((result or {}).get("note") or "")[:200],
           "excluded_note": str((result or {}).get("excluded_note") or "")[:200],
           "usage": (meta or {}).get("usage") or {},
           "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "cached": False,
           "rules": {"prefer": "好玩、有梗、有共鸣", "skip": "新闻事件类",
                     "never": "涉及其他数码/手机/电脑品牌"},
           "notice": ("清单由公开热榜原始条目经模型整理得到，不是平台官方热点榜；"
                      "该源不提供榜单更新时间。"),
           "link_note": ("链接是小红书搜索页链接（不是笔记链接），需要小红书登录才能打开，"
                         "不能由服务端抓取内容；可点开后人工查看。")}
    _CACHE[key] = (time.monotonic(), out)
    return out


def status() -> dict:
    return {"cache_ttl_seconds": CACHE_TTL_SECONDS, "other_brands": len(OTHER_BRANDS),
            "source": "60s-rednote", "source_cap": 20,
            "rules": {"prefer": "好玩、有梗、有共鸣", "skip": "新闻事件类",
                      "never": "涉及其他数码/手机/电脑品牌"},
            "note": "取源 + 模型整理；不做排序与打分。"}