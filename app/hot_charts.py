"""公开热榜原始条目（抖音/微博/知乎/小红书），不带登录、不带编辑兜底。

这是「其他热点榜」的数据源：给用户看各平台当下真实在榜的条目。它不产出热梗——实测四个榜单
里几乎没有梗（抖音 1/49，微博 0/50，知乎 0/30），热梗要靠 app/web_search.py 联网搜索。
"""
from __future__ import annotations

import json
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from zoneinfo import ZoneInfo

USER_AGENT = "huawei-pc-marketing-agent/0.6"
SHANGHAI = ZoneInfo("Asia/Shanghai")

# 只用实测可用的：baidu 404、bili 500、toutiao 超时
FEEDS = {
    "douyin": ("抖音热榜", "https://60s.viki.moe/v2/douyin"),
    "weibo": ("微博热搜", "https://60s.viki.moe/v2/weibo"),
    "zhihu": ("知乎热榜", "https://60s.viki.moe/v2/zhihu"),
    "rednote": ("小红书热榜", "https://60s.viki.moe/v2/rednote"),
}


def _get(url, timeout=8):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                                   "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read(2_000_000))


def _items(payload):
    data = payload.get("data") if isinstance(payload, dict) else payload
    if isinstance(data, dict):
        for key in ("items", "list", "questions"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
    return data if isinstance(data, list) else []


def fetch(platform, limit=50):
    """一个平台的原始榜单。失败返回空列表，由调用方如实报告。"""
    entry = FEEDS.get(platform)
    if not entry:
        return []
    _, url = entry
    try:
        payload = _get(url)
    except Exception:  # noqa: BLE001 - 源不稳定是常态（实测成功率约 50%）
        return []
    rows = []
    for index, item in enumerate(_items(payload)[:limit]):
        title = item.get("title") or item.get("name") or ""
        if not title:
            continue
        rows.append({"rank": item.get("rank") or index + 1, "title": str(title)[:120],
                     "hot": item.get("hot_value") or item.get("score") or item.get("hot"),
                     "url": item.get("link") or "", "platform": platform})
    return rows


def fetch_many(platforms=None, limit=50):
    """并发取多个平台，逐平台如实报告成功与否。"""
    names = list(platforms or FEEDS.keys())
    result = {}
    with ThreadPoolExecutor(max_workers=min(4, len(names))) as pool:
        futures = {pool.submit(fetch, name, limit): name for name in names}
        for future, name in futures.items():
            try:
                result[name] = future.result(timeout=20)
            except Exception:  # noqa: BLE001
                result[name] = []
    boards = []
    for name in names:
        label = FEEDS[name][0]
        rows = result.get(name) or []
        boards.append({"platform": name, "label": label, "count": len(rows),
                       "items": rows, "ok": bool(rows),
                       "note": "" if rows else "该源本次没有返回内容（实测该源可用率约 50%）"})
    return {"ok": any(b["ok"] for b in boards), "boards": boards,
            "fetched_at": datetime.now(SHANGHAI).isoformat(timespec="seconds"),
            "notice": "各平台当下在榜条目原文；榜单报的是事件，不是热梗。"}
