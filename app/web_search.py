"""Keyless web search, so the agent can read what is actually being said.

Why this exists, in one measurement: asked for recent memes, the main model **refused honestly**
("我的知识截止时间早于该时段……不能凭推测编造", zero topics), because "what is trending now" is
simply not in its weights. A chart does not help either - four charts were checked and none is a
meme corpus:

===========  ==============================  ==============
chart        content                         items about memes
===========  ==============================  ==============
小红书        生活（遛狗/拼豆/拍照）              0 / 20
微博          新闻/娱乐/体育                    0 / 50
抖音          新闻/体育/赛事                    1 / 49
知乎          新闻问答                         0 / 30
===========  ==============================  ==============

Memes live in articles, notes and discussions, not in rankings, so finding them needs a **search**.
The web version of the same model produced a good, well-grounded list because it could search; the
difference was never the prompt.

It also fixes a subtler problem: with only bare titles as input, a 100-character intro could only be
*fabricated* ("讨论集中在这样遛到底行不行" - invented, yet every intro read plausibly). Search
snippets give real sentences to work from.

No API key: DuckDuckGo lite renders plain HTML tables, with Bing's HTML page as a fallback.
"""
from __future__ import annotations

import html
import re
import time
import urllib.parse
import urllib.request

USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")

# class='result-link' is single-quoted in DDG lite's output, which is why a double-quoted pattern
# silently matched nothing on the first attempt.
_DDG_LINK = re.compile(r"<a[^>]+href=\"(?P<href>[^\"]+)\"[^>]*class='result-link'>(?P<title>.*?)</a>",
                       re.S)
_DDG_SNIPPET = re.compile(r"class='result-snippet'>(?P<text>.*?)</td>", re.S)
# Bing puts <link> tags straight after the <li>, and its results nest lists, so splitting into
# blocks beats one non-greedy regex that stops at the first nested </li>.
_BING_SPLIT = re.compile(r'<li class="b_algo"')
_BING_H2 = re.compile(r"<h2[^>]*>(?P<title>.*?)</h2>", re.S)
_BING_HREF = re.compile(r'href="(?P<href>https?://[^"]+)"')
_BING_P = re.compile(r"<p[^>]*>(?P<text>.*?)</p>", re.S)


def _clean(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", str(text or ""))).replace("\xa0", " ").strip()


def _unwrap(url: str) -> str:
    """DDG wraps results as //duckduckgo.com/l/?uddg=<encoded>; return the real target."""
    if "uddg=" not in url:
        return url if url.startswith("http") else "https:" + url
    query = urllib.parse.parse_qs(urllib.parse.urlparse("https:" + url if url.startswith("//") else url).query)
    return urllib.parse.unquote((query.get("uddg") or [""])[0]) or url


def _get(url: str, timeout: float) -> str:
    request = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read(2_000_000).decode("utf-8", "ignore")


def _parse_ddg(body: str) -> list[dict]:
    rows, snippets = [], [_clean(m) for m in _DDG_SNIPPET.findall(body)]
    for index, match in enumerate(_DDG_LINK.finditer(body)):
        rows.append({"title": _clean(match.group("title")),
                     "url": _unwrap(match.group("href")),
                     "snippet": snippets[index] if index < len(snippets) else ""})
    return rows


def _parse_bing(body: str) -> list[dict]:
    rows = []
    chunks = _BING_SPLIT.split(body)[1:]
    for chunk in chunks:
        heading = _BING_H2.search(chunk)
        if not heading:
            continue
        href = _BING_HREF.search(heading.group("title")) or _BING_HREF.search(chunk)
        paragraphs = _BING_P.findall(chunk)
        rows.append({"title": _clean(heading.group("title")),
                     "url": html.unescape(href.group("href")) if href else "",
                     "snippet": _clean(paragraphs[0])[:400] if paragraphs else ""})
    return rows


def search(query: str, *, limit: int = 8, timeout: float = 15.0) -> dict:
    """One query, two providers tried in order. Reports which one answered."""
    query = str(query or "").strip()
    if not query:
        return {"ok": False, "error": "EMPTY_QUERY", "results": []}
    attempts = (
        ("duckduckgo-lite", "https://lite.duckduckgo.com/lite/?q=" + urllib.parse.quote(query), _parse_ddg),
        ("bing-html", "https://www.bing.com/search?q=" + urllib.parse.quote(query), _parse_bing),
    )
    errors = []
    for name, url, parser in attempts:
        try:
            rows = parser(_get(url, timeout))
        except Exception as error:  # noqa: BLE001 - a provider being down is expected, not fatal
            errors.append(f"{name}: {type(error).__name__}")
            continue
        rows = [row for row in rows if row["title"] and row["url"]]
        if rows:
            return {"ok": True, "provider": name, "query": query, "results": rows[:limit],
                    "attempted": [item[0] for item in attempts], "errors": errors,
                    "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
        errors.append(f"{name}: 解析到 0 条")
    return {"ok": False, "error": "NO_RESULTS", "query": query, "results": [],
            "errors": errors, "attempted": [item[0] for item in attempts]}


def search_many(queries: list[str], *, limit_each: int = 6) -> dict:
    """Several queries, de-duplicated by URL. Used to cover one topic from a few angles."""
    seen, results, providers, errors = set(), [], [], []
    for query in queries:
        outcome = search(query, limit=limit_each)
        if outcome.get("ok"):
            providers.append(outcome.get("provider"))
            for row in outcome["results"]:
                if row["url"] not in seen:
                    seen.add(row["url"])
                    results.append({**row, "from_query": query})
        else:
            errors.extend(outcome.get("errors") or [outcome.get("error")])
    return {"ok": bool(results), "results": results, "providers": sorted(set(providers)),
            "queries": list(queries), "errors": errors[:6],
            "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}