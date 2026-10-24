"""本次任务的资料档案（可累加）。

用户确认的规格 ``docs/新对话流程规格.md`` 第 2 节：**目标人群 / 发布平台 / 使用环境·场景 /
要打的活动 / 选定型号 / 参考进对话的热点** 构成本次任务的资料；用户中途补充就**并进资料**，
后续产出都带着它。

存储位置复用 ``models.AgentTaskState.data``，不新造并行存储::

    data["material"] = {
        "audience": "", "platform": "", "scenario": "", "campaign": "",
        "models": [{"product_id": "P-xxx", "model": "HUAWEI …"}],
        "hotspot": {"title": "", "source": "", ...},
    }

同时写出兼容键（旧代码/旧数据仍可读）：

* ``data["selected_models"]`` —— product_id 列表（规格建议的显式存法）
* ``data["audience"]`` / ``data["scenario"]`` —— 工作室等处此前直接读的键
* 旧结构 ``data["candidates"][0]``、``data["needs"]``、``data["selected"]``、``data["topic"]``
  在 :func:`normalize` 里一并读取，保证旧任务不会读不出来。

字段名按规格固定，不自行改名；缺数据就是空串/空数组，不编造。
"""
from __future__ import annotations

# 契约里的 material 形状：只保留这些键，值缺失时留空。
_MATERIAL_TEXT_FIELDS = ("audience", "platform", "scenario", "campaign")
_HOTSPOT_TEXT_FIELDS = ("title", "source", "url", "kind", "gist", "intro")
_HOTSPOT_LIST_FIELDS = ("keywords",)


def _text(value) -> str:
    return str(value or "").strip()


def _dict(value) -> dict:
    return dict(value) if isinstance(value, dict) else {}


def _list(value) -> list:
    if isinstance(value, (list, tuple)):
        return list(value)
    return []


def _models_from_candidates(candidates) -> list[dict]:
    rows: list[dict] = []
    for row in _list(candidates):
        if isinstance(row, dict) and _text(row.get("product_id")):
            rows.append({"product_id": _text(row.get("product_id")),
                         "model": _text(row.get("model"))})
    return rows


def _normalize_model_rows(value) -> list[dict]:
    """Accept either {"product_id","model"} rows or bare product-id/model strings."""
    rows: list[dict] = []
    for item in _list(value):
        if isinstance(item, dict):
            product_id = _text(item.get("product_id"))
            model = _text(item.get("model"))
            if product_id or model:
                rows.append({"product_id": product_id, "model": model})
        elif _text(item):
            rows.append({"product_id": _text(item), "model": ""})
    return rows


def _dedupe_models(rows: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for row in rows:
        key = row.get("product_id") or row.get("model")
        if not key or key in seen:
            continue
        seen.add(key)
        out.append({"product_id": row.get("product_id", ""), "model": row.get("model", "")})
    return out


def normalize(data: dict | None) -> dict:
    """Read the archive from any of the structures this codebase has used."""
    data = _dict(data)
    raw = _dict(data.get("material"))
    needs = _dict(data.get("needs"))
    candidates = _models_from_candidates(data.get("candidates"))
    by_id = {row["product_id"]: row for row in candidates}

    models = _normalize_model_rows(raw.get("models"))
    if not models:
        # 显式选型（规格建议的存法）优先，其次本轮 guided 的 selected。
        for product_id in _list(data.get("selected_models")):
            row = by_id.get(_text(product_id))
            models.append(row or {"product_id": _text(product_id), "model": ""})
    if not models and isinstance(data.get("selected"), dict):
        models = _normalize_model_rows([data["selected"]])
    if not models and len(candidates) == 1 and not data.get("flow"):
        # 旧结构兜底：新流程之前只存了 candidates。约定 candidates[0] 就是已定产品**只在候选唯一
        # 时成立**——唯一候选没有第二种可能，所以是"真正可判定"的旧数据。
        # 候选有多条时（实测 6 条旧会话：候选是 MateBook 14 鸿蒙版/Fold/Pro 三条，用户实际选的
        # D 14 根本不在候选里）取 candidates[0] 是凭空捏造一个用户没选过的型号，直接违反"不猜"。
        # 这种情况宁可留空（selected_models: []），也不把候选第一位冒充成选择。
        models = [candidates[0]]
    models = _dedupe_models(models)

    hotspot = dict(_dict(raw.get("hotspot")))
    if not _text(hotspot.get("title")) and _text(data.get("hotspot")):
        hotspot["title"] = _text(data.get("hotspot"))
    if not _text(hotspot.get("title")) and _text(needs.get("topic")):
        hotspot["title"] = _text(needs.get("topic"))

    material = {
        "audience": _text(raw.get("audience")) or _text(data.get("audience"))
                    or _text(needs.get("audience")),
        "platform": _text(raw.get("platform")) or _text(data.get("platform"))
                    or _text(needs.get("platform")),
        "scenario": _text(raw.get("scenario")) or _text(data.get("scenario"))
                    or _text(needs.get("scenario")),
        "campaign": _text(raw.get("campaign")) or _text(data.get("campaign"))
                    or _text(needs.get("campaign")),
        "models": models,
        "hotspot": hotspot,
    }
    return material


def payload(material: dict | None) -> dict:
    """The exact contract shape of ``material`` (field names never change)."""
    material = _dict(material)
    hotspot = _dict(material.get("hotspot"))
    return {
        "audience": _text(material.get("audience")),
        "platform": _text(material.get("platform")),
        "scenario": _text(material.get("scenario")),
        "campaign": _text(material.get("campaign")),
        "models": [{"product_id": _text(row.get("product_id")), "model": _text(row.get("model"))}
                   for row in _normalize_model_rows(material.get("models"))
                   if _text(row.get("product_id")) or _text(row.get("model"))],
        "hotspot": {"title": _text(hotspot.get("title")), "source": _text(hotspot.get("source"))},
    }


def selected_ids(material: dict | None) -> list[str]:
    return [row["product_id"] for row in _normalize_model_rows(_dict(material).get("models"))
            if _text(row.get("product_id"))]


def merge(data: dict | None, patch: dict | None, *, resolver=None) -> dict:
    """Merge one patch into the archive and mirror the compatibility keys.

    ``audience``/``platform``/``scenario``/``campaign``/hotspot **accumulate**: an empty value
    never erases what is already known. ``models`` is a *choice*, so a non-empty list replaces
    the current selection (1–2 models) instead of piling up. ``resolver`` turns a product id or
    model name into ``{"product_id", "model"}``; when it returns ``None`` the raw value is kept
    so nothing is invented and nothing is silently dropped.
    """
    data = data if isinstance(data, dict) else {}
    patch = _dict(patch)
    material = normalize(data)

    for key in _MATERIAL_TEXT_FIELDS:
        value = _text(patch.get(key))
        if value:
            material[key] = value

    raw_models = _normalize_model_rows(patch.get("models"))
    if raw_models:
        resolved: list[dict] = []
        for row in raw_models:
            value = row.get("product_id") or row.get("model")
            found = resolver(value) if callable(resolver) else None
            if isinstance(found, dict) and (_text(found.get("product_id")) or _text(found.get("model"))):
                resolved.append({"product_id": _text(found.get("product_id")),
                                 "model": _text(found.get("model"))})
            else:
                resolved.append(row)
        material["models"] = _dedupe_models(resolved)

    hotspot_patch = _dict(patch.get("hotspot"))
    if hotspot_patch:
        current = dict(material.get("hotspot") or {})
        new_title = _text(hotspot_patch.get("title"))
        # 换了一个新热点就整体替换，避免上一个热点的 gist/keywords 粘在新热点上。
        hotspot = {} if (new_title and new_title != _text(current.get("title"))) else current
        for key in _HOTSPOT_TEXT_FIELDS:
            value = _text(hotspot_patch.get(key))
            if value:
                hotspot[key] = value
        for key in _HOTSPOT_LIST_FIELDS:
            value = [_text(item) for item in _list(hotspot_patch.get(key)) if _text(item)]
            if value:
                hotspot[key] = value
        material["hotspot"] = hotspot

    data["material"] = material
    data["selected_models"] = selected_ids(material)
    # Marks the task as written by the new (3-variant) flow, so the legacy
    # "candidates[0] is the chosen product" fallback can never fire for it.
    data["flow"] = "variants_v1"
    data["audience"] = material["audience"]
    data["scenario"] = material["scenario"]
    if material.get("platform"):
        data["platform"] = material["platform"]
    return material


def project_variants(variants, *, fallback_models=None) -> list[dict]:
    """Project saved variants to the exact delivery-contract shape.

    ``variants[i].models`` is a list of **product_id strings**; the model name is not repeated
    here because ``material.models`` already carries it. The fallback only applies when the
    row has **no** ``models`` key at all (older artifacts). An explicit empty list means
    "the author did not state a per-post model", and must not be expanded to every selected
    id — that would claim each post belongs to both models at once.
    """
    fallback = [_text(item) for item in _list(fallback_models) if _text(item)]
    out: list[dict] = []
    for index, raw in enumerate(_list(variants), 1):
        if not isinstance(raw, dict):
            continue
        models = [_text(item) for item in _list(raw.get("models")) if _text(item)]
        if not models and "models" not in raw:
            models = list(fallback)
        tags = [_text(item) for item in _list(raw.get("tags")) if _text(item)]
        out.append({
            "variant_id": _text(raw.get("variant_id")) or f"v{index}",
            "label": _text(raw.get("label")) or f"方向{index}",
            "title": _text(raw.get("title")),
            "body": str(raw.get("body") or ""),
            "tags": tags,
            "models": models,
        })
    return out


def variants_from_content(content: dict | None) -> list[dict]:
    """Read variants out of a saved artifact's content, if it carries any."""
    content = _dict(content)
    rows = content.get("variants")
    if not isinstance(rows, list) or not rows:
        return []
    return project_variants(rows, fallback_models=content.get("product_ids"))


def hotspot_from_body(body: dict | None) -> dict:
    """Normalise a hotspot payload (route or tool argument) to the stored shape."""
    body = _dict(body)
    hotspot: dict = {}
    for key in _HOTSPOT_TEXT_FIELDS:
        value = _text(body.get(key))
        if value:
            hotspot[key] = value
    for key in _HOTSPOT_LIST_FIELDS:
        value = [_text(item) for item in _list(body.get(key)) if _text(item)]
        if value:
            hotspot[key] = value
    return hotspot