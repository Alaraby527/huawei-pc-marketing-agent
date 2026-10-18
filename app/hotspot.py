"""Optional, best-effort public hot-topic reference (60s.viki.moe rednote feed).

This is advisory only: it never gates the main pipeline, never claims to be
a verified real-time ranking at generation time, and is skipped silently on
any network/parse failure. It must not be used as a source of product facts.
"""
import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

ENDPOINT_BASE = os.getenv("HOT_API_BASE", "https://60s.viki.moe").rstrip("/")
SHANGHAI = ZoneInfo("Asia/Shanghai")
CURATED_ANGLES = [
    {"title": "开学季：学习、携带与续航怎么平衡", "rank": 1},
    {"title": "职场办公：会议、差旅和多任务的一天", "rank": 2},
    {"title": "内容创作：屏幕、素材流转和剪辑流程", "rank": 3},
    {"title": "家庭换新：旧机迁移和全家共用", "rank": 4},
    {"title": "企业采购：部署、外设和稳定性", "rank": 5},
    {"title": "门店到访：一个卖点和一个到店理由", "rank": 6},
]


def _fetch_source(platform, limit=50):
    try:
        req = urllib.request.Request(ENDPOINT_BASE + "/v2/" + platform,
                                     headers={"Accept": "application/json",
                                              "User-Agent": "huawei-pc-marketing-agent/0.6"})
        with urllib.request.urlopen(req, timeout=6) as response:
            data = json.loads(response.read(1_000_000))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return []
    items = data.get("data") if isinstance(data, dict) else data
    if isinstance(items, dict):
        items = items.get("items") or items.get("list") or []
    if not isinstance(items, list):
        return []
    topics = []
    for index, item in enumerate(items[:limit]):
        if not isinstance(item, dict):
            continue
        title = item.get("title") or item.get("name") or ""
        if not isinstance(title, str) or not title.strip():
            continue
        topics.append({
            "title": title.strip()[:120],
            "heat": item.get("score") or item.get("hot_value") or item.get("heat"),
            "rank": item.get("rank", index + 1),
            "url": item.get("link") or item.get("url") or "",
            "source": "60s-" + platform,
            "platform": platform,
            "observed_at": datetime.now(SHANGHAI).isoformat(timespec="seconds"),
        })
    return topics


def _fetch(limit=50):
    # The Xiaohongshu-specific feed is preferred. Weibo is a clearly labelled
    # cross-platform fallback when the rednote upstream is unavailable.
    for platform in ("rednote", "weibo"):
        topics = _fetch_source(platform, limit)
        if topics:
            return topics
    return []


def related_hot_topics(scenario, audience, limit=6):
    """Return loosely related topics; curated angles when public feeds are down."""
    topics = _fetch()
    if not topics:
        return [{**topic, "source": "editorial-fallback", "data_mode": "editorial_fallback",
                 "observed_at": datetime.now(SHANGHAI).isoformat(timespec="seconds"),
                 "relation": "editorial", "heat": None, "url": ""}
                for topic in CURATED_ANGLES[:limit]]
    live_mode = "live" if any(topic.get("platform") == "rednote" for topic in topics) else "live_fallback"
    terms = [w for w in re.split(r"[，、\s]+", (scenario or "") + " " + (audience or "")) if len(w) >= 2]
    scored = []
    for topic in topics:
        hits = sum(1 for term in terms if term in topic["title"])
        scored.append((hits, topic))
    scored.sort(key=lambda item: (-item[0], item[1].get("rank") or 999))
    result = []
    for hits, topic in scored[:limit]:
        result.append({**topic, "data_mode": live_mode, "relation": "matched" if hits else "weak"})
    return result


def related_hot_terms(scenario, audience, limit=3):
    return [topic["title"] for topic in related_hot_topics(scenario, audience, limit)]
