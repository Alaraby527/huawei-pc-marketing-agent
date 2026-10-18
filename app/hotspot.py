"""Optional, best-effort public hot-topic reference (60s.viki.moe rednote feed).

This is advisory only: it never gates the main pipeline, never claims to be
a verified real-time ranking at generation time, and is skipped silently on
any network/parse failure. It must not be used as a source of product facts.
"""
import os
import re
import urllib.error
import urllib.request
import json

ENDPOINT = os.getenv("HOT_API_BASE", "https://60s.viki.moe").rstrip("/") + "/v2/rednote"


def related_hot_terms(scenario, audience, limit=3):
    """Return up to `limit` loosely related public hot-topic titles, or [] on any failure."""
    try:
        req = urllib.request.Request(ENDPOINT, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=6) as response:
            raw = response.read(1_000_000)
        data = json.loads(raw)
        items = data.get("data", [])
        if not isinstance(items, list):
            return []
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, AttributeError):
        return []
    terms = [w for w in re.split(r"[，、\s]+", (scenario or "") + " " + (audience or "")) if w]
    scored = []
    for item in items:
        title = item.get("title", "")
        if not isinstance(title, str) or not title:
            continue
        hits = sum(1 for t in terms if t and t in title)
        if hits > 0:
            scored.append((hits, title))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [{"title": t, "note": "来自60s公开热榜的轻关联参考，非实时验证结果，不作为产品事实或发布承诺"}
            for _, t in scored[:limit]]
