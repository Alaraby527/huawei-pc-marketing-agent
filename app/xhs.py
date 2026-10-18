"""Optional Xiaohongshu MCP adapter.

The MCP service is intentionally external: it owns a logged-in browser session
and must be able to show a QR code to the operator. This module only proxies
read-only endpoints and never stores a password.
"""
import asyncio
import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo


class XhsError(Exception):
    def __init__(self, message, status=502):
        super().__init__(message)
        self.message = message
        self.status = status


def base_url():
    return (os.getenv("XHS_MCP_BASE") or "").rstrip("/")


def token():
    return os.getenv("XHS_MCP_TOKEN", "")


def configured():
    return bool(base_url())


def _request(path, payload=None, method=None):
    base = base_url()
    if not base:
        raise XhsError("当前实例未配置小红书 MCP，请先使用公开热榜或编辑方向", 503)
    headers = {"Accept": "application/json", "User-Agent": "huawei-pc-marketing-agent/0.6"}
    if token():
        headers["Authorization"] = "Bearer " + token()
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(base + path, data=data, headers=headers,
                                     method=method or ("POST" if data else "GET"))
    try:
        with urllib.request.urlopen(request, timeout=80) as response:
            raw = response.read(2_000_000)
    except urllib.error.HTTPError as error:
        if error.code == 401:
            raise XhsError("小红书 MCP 鉴权失败，请检查 XHS_MCP_TOKEN", 502) from None
        raise XhsError("小红书 MCP HTTP " + str(error.code), 502) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise XhsError("小红书 MCP 连接失败或超时，请确认服务已启动并已登录", 502) from None
    try:
        result = json.loads(raw)
    except ValueError:
        raise XhsError("小红书 MCP 返回了非 JSON 响应", 502) from None
    if result.get("success") is False:
        raise XhsError("小红书 MCP 返回失败", 502)
    return result.get("data", result)


async def login_status():
    if not configured():
        return {"configured": False, "logged_in": False}
    data = await asyncio.to_thread(_request, "/api/v1/login/status")
    return {"configured": True, "logged_in": bool(data.get("is_logged_in"))}


async def login_qrcode():
    data = await asyncio.to_thread(_request, "/api/v1/login/qrcode")
    image = data.get("img", "") or ""
    if image and not image.startswith("data:image/"):
        try:
            decoded = base64.b64decode(image, validate=True)
        except (ValueError, TypeError):
            decoded = b""
        if decoded.startswith(b"\x89PNG"):
            image = "data:image/png;base64," + base64.b64encode(decoded).decode()
        else:
            image = ""
    return {
        "is_logged_in": bool(data.get("is_logged_in")),
        "qr_image": image[:400000],
        "timeout": data.get("timeout", ""),
    }


def _feed_topics(feeds, scenario, audience):
    terms = [term for term in (scenario + " " + audience).split() if len(term) >= 2]
    topics = []
    for index, feed in enumerate(feeds[:10]):
        if not isinstance(feed, dict):
            continue
        card = feed.get("noteCard") or {}
        title = card.get("displayTitle") or ""
        if not title:
            continue
        metrics = card.get("interactInfo") or {}
        feed_id = feed.get("id") or ""
        xsec = feed.get("xsecToken") or ""
        url = "https://www.xiaohongshu.com/explore/" + feed_id
        if xsec:
            url += "?" + urllib.parse.urlencode({"xsec_token": xsec, "xsec_source": "pc_search"})
        hits = sum(1 for term in terms if term in title)
        topics.append({
            "title": title[:120],
            "heat": metrics.get("likedCount"),
            "rank": index + 1,
            "url": url,
            "source": "xiaohongshu-mcp",
            "platform": "xiaohongshu",
            "data_mode": "live_xhs",
            "observed_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
            "relation": "matched" if hits else "weak",
        })
    return topics


async def hot_topics(scenario, audience, limit=6):
    if not configured():
        raise XhsError("当前实例未配置小红书 MCP", 503)
    status = await login_status()
    if not status.get("logged_in"):
        raise XhsError("请先扫码登录小红书并完成授权", 401)
    keywords = [" ".join(part for part in [scenario, audience, "华为笔记本"] if part).strip(),
                "华为笔记本", "笔记本电脑"]
    for keyword in dict.fromkeys(keywords):
        data = await asyncio.to_thread(
            _request, "/api/v1/feeds/search",
            {"keyword": keyword, "filters": {"sort_by": "最新", "note_type": "图文", "publish_time": "一周内"}})
        feeds = data.get("feeds") or []
        topics = _feed_topics(feeds, scenario or "", audience or "")
        if topics:
            return topics[:limit]
    return []
