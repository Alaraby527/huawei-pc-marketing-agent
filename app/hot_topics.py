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

每条只输出四个字段：
- topic：热点名称，简短
- kind：玩梗 / 生活方式 / 共鸣场景 / 其他
- gist：一句话说明它是什么
- source_titles：对应的原始条目标题（数组，必须来自给定条目）

只输出 JSON：
{"topics":[...],"excluded_note":"一句话说明剔除了哪几类","note":"一句话说明这份清单的性质"}"""

# Refresh is explicit (a button), so a short cache only prevents double-clicking from paying twice.
CACHE_TTL_SECONDS = int(os.getenv("HOT_TOPICS_TTL_SECONDS", "600") or 600)
_CACHE: dict[str, tuple[float, dict]] = {}


def reset_cache() -> None:
    _CACHE.clear()


def hits_other_brand(text: str) -> str:
    """Return the matched brand, or an empty string. Used before and after the model."""
    low = str(text or "").lower()
    for brand in OTHER_BRANDS:
        if brand in low:
            return brand.strip()
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
        topics.append({"topic": str(row.get("topic") or "")[:80],
                       "kind": str(row.get("kind") or KIND_OTHER)[:12],
                       "gist": str(row.get("gist") or "")[:200],
                       "source_titles": titles[:6]})
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
                      "该源不提供榜单更新时间。")}
    _CACHE[key] = (time.monotonic(), out)
    return out


def status() -> dict:
    return {"cache_ttl_seconds": CACHE_TTL_SECONDS, "other_brands": len(OTHER_BRANDS),
            "source": "60s-rednote", "source_cap": 20,
            "rules": {"prefer": "好玩、有梗、有共鸣", "skip": "新闻事件类",
                      "never": "涉及其他数码/手机/电脑品牌"},
            "note": "取源 + 模型整理；不做排序与打分。"}