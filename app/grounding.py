"""Field-level grounding for product claims.

The old compliance pass only checked that every number appeared somewhere in
the allowed set, which allowed cross-variant mixes ("32 GB" appearing in a
different configuration) and lost qualifiers. This module binds each numeric
claim to a concrete product fact (product / variant / field / value / unit /
qualifier / conditions) and reports precise errors.

It is intentionally conservative about what counts as a product claim:
numbers without a product unit (page counts, title counts, dates, ratings)
are ignored; price-like units are rejected unless a price fact exists.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

# Unit families used for controlled conversion. Only exact conversions are
# accepted; approximations keep their qualifier.
_UNIT_FACTORS = {
    "kb": ("byte", 1 / 1024),
    "mb": ("byte", 1),
    "gb": ("byte", 1024),
    "tb": ("byte", 1024 * 1024),
    "g": ("weight", 1),
    "kg": ("weight", 1000),
    "mg": ("weight", 0.001),
    "mm": ("length", 1),
    "cm": ("length", 10),
    "m": ("length", 1000),
    "wh": ("energy", 1),
    "kwh": ("energy", 1000),
    "hz": ("frequency", 1),
    "khz": ("frequency", 1000),
    "ghz": ("frequency", 1000),
    "英寸": ("inch", 1),
    "寸": ("inch", 1),
    "元": ("price", 1),
}
_UNIT_ALIASES = {"gb": "gb", "g": "g", "tb": "tb", "mb": "mb", "kb": "kb",
                 "kg": "kg", "千克": "kg", "公斤": "kg", "克": "g",
                 "mm": "mm", "毫米": "mm", "cm": "cm", "厘米": "cm",
                 "wh": "wh", "瓦时": "wh", "kwh": "kwh",
                 "hz": "hz", "khz": "khz", "ghz": "ghz",
                 "英寸": "英寸", "寸": "寸", "元": "元"}

_NUMBER = r"\d+(?:\.\d+)?"
_UNIT_PATTERN = "|".join(sorted((re.escape(unit) for unit in _UNIT_ALIASES), key=len, reverse=True))
_CLAIM_RE = re.compile(r"(?P<number>" + _NUMBER + r")\s*(?P<unit>" + _UNIT_PATTERN + r")(?![a-zA-Z])", re.IGNORECASE)
_QUALIFIERS = ("约", "大约", "最高", "至高", "选配", "仅", "支持", "最长", "最低", "起")
_FIELD_HINTS = ("内存", "存储", "硬盘", "重量", "屏幕", "尺寸", "电池", "容量", "分辨率",
                "刷新率", "接口", "续航", "处理器", "显卡", "摄像头", "厚度")
_FIELD_ALIASES = {
    "厚度": "尺寸与重量", "重量": "尺寸与重量", "尺寸": "尺寸与重量",
    "刷新率": "屏幕", "分辨率": "屏幕", "屏幕": "屏幕",
    "电池": "电池", "续航": "电池", "容量": "电池",
    "存储": "存储", "硬盘": "存储", "内存": "内存", "接口": "接口",
    "处理器": "处理器", "显卡": "显卡", "摄像头": "摄像头",
}


@dataclass
class FactRecord:
    fact_id: str
    product_id: str
    variant_id: str
    field: str
    value: str
    unit: str
    qualifier: str = ""
    conditions: list[str] = field(default_factory=list)
    source_id: str = ""
    source_revision: str = ""
    source_locator: str = ""
    raw_text: str = ""

    def as_dict(self) -> dict:
        return {"fact_id": self.fact_id, "product_id": self.product_id,
                "variant_id": self.variant_id, "field": self.field,
                "value": self.value, "unit": self.unit, "qualifier": self.qualifier,
                "conditions": self.conditions, "source_id": self.source_id,
                "source_revision": self.source_revision,
                "source_locator": self.source_locator, "raw_text": self.raw_text}


def _unit_key(unit: str) -> str:
    return _UNIT_ALIASES.get(unit.strip().lower(), unit.strip().lower())


def _normalized(value: str, unit: str) -> tuple[float, str] | None:
    key = _unit_key(unit)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    family, factor = _UNIT_FACTORS.get(key, (key, 1))
    return number * factor, family


def _clean_conditions(value) -> list[str]:
    """Keep applicability conditions, drop source/provenance boilerplate."""
    if isinstance(value, (list, tuple)):
        parts = [str(item) for item in value]
    else:
        parts = re.split(r"[；;。\n]", str(value or ""))
    keep: list[str] = []
    for part in parts:
        text = part.strip()
        if not text:
            continue
        if any(marker in text for marker in ("来源：", "抓取日期", "官网", "http://", "https://")):
            continue
        if any(marker in text for marker in ("配置可能因销售版本而异", "以实际销售版本为准",
                                             "配置因销售版本而异", "不同模式", "实际可视区域")):
            continue
        if any(marker in text for marker in ("仅", "版本", "配置", "电池", "键盘", "支持",
                                             "默认", "选配", "适用", "条件", "最高")):
            if text not in keep:
                keep.append(text)
    return keep


def records_from_facts(facts: Iterable, product_id: str = "", variant_id: str = "") -> list[FactRecord]:
    """Convert the existing Fact objects (text + source + conditions) into records.

    The raw text is preserved; parsing is conservative and only splits the
    first `：`/`:` separator.
    """
    records: list[FactRecord] = []
    for fact in facts:
        text = str(getattr(fact, "text", "") or getattr(fact, "get", lambda *_: "")("text", ""))
        fact_id = str(getattr(fact, "fact_id", "") or (fact.get("fact_id") if isinstance(fact, dict) else ""))
        source = getattr(fact, "source", None) or (fact.get("source") if isinstance(fact, dict) else {}) or {}
        source_title = str(getattr(source, "title", "") or (source.get("title") if isinstance(source, dict) else ""))
        locator = str(getattr(source, "locator", "") or (source.get("locator") if isinstance(source, dict) else ""))
        raw_conditions = getattr(fact, "conditions", "") or (fact.get("conditions") if isinstance(fact, dict) else "") or ""
        conditions = _clean_conditions(raw_conditions)
        parts = re.split(r"[：:]", text, maxsplit=1)
        label = parts[0].strip()
        value = parts[1].strip() if len(parts) > 1 else text.strip()
        matches = list(_CLAIM_RE.finditer(value))
        for match in matches:
            context = value[max(0, match.start() - 10):min(len(value), match.end() + 18)]
            qualifier = next((word for word in _QUALIFIERS if word in context), "")
            extra = re.findall(r"[（(]([^）)]{1,30})[）)]", context)
            records.append(FactRecord(fact_id=fact_id, product_id=product_id, variant_id=variant_id,
                                      field=label, value=match.group("number"),
                                      unit=match.group("unit"), qualifier=qualifier,
                                      conditions=list(dict.fromkeys(conditions + extra)),
                                      source_id=source_title,
                                      source_revision=str(getattr(fact, "source_revision", "") or ""),
                                      source_locator=locator, raw_text=text))
    return records


@dataclass
class Claim:
    text: str
    number: str
    unit: str
    start: int
    end: int
    context: str = ""


def extract_claims(text: str) -> list[Claim]:
    claims: list[Claim] = []
    for match in _CLAIM_RE.finditer(text):
        start, end = match.start(), match.end()
        context = text[max(0, start - 18):min(len(text), end + 18)]
        claims.append(Claim(text=match.group(0), number=match.group("number"),
                            unit=match.group("unit"), start=start, end=end, context=context))
    return claims


def _field_hint(context: str) -> str:
    for hint in _FIELD_HINTS:
        if hint in context:
            return hint
    return ""


def _clause(text: str, start: int, end: int) -> str:
    separators = "，。；、,;\n"
    left = max((text.rfind(char, 0, start) for char in separators), default=-1) + 1
    right_positions = [text.find(char, end) for char in separators]
    right_positions = [position for position in right_positions if position != -1]
    right = min(right_positions) if right_positions else len(text)
    return text[left:right]


def check_text(text: str, records: list[FactRecord], *, strict_qualifier: bool = False) -> dict:
    """Check numeric product claims in one string.

    Returns {passed, errors, warnings, checked}. Errors are hard mismatches;
    warnings are ambiguous or lost-qualifier issues.
    """
    errors: list[dict] = []
    warnings: list[dict] = []
    claims = extract_claims(text)
    for claim in claims:
        normalized = _normalized(claim.number, claim.unit)
        if normalized is None:
            continue
        value, family = normalized
        if family == "price":
            errors.append({"code": "PRICE_CLAIM", "message": "不允许写价格，除非有当前有效价格证据",
                           "claim": claim.text, "span": [claim.start, claim.end]})
            continue
        candidates = [record for record in records
                      if _normalized(record.value, record.unit) and
                      _normalized(record.value, record.unit)[1] == family]
        exact = [record for record in candidates
                 if abs(_normalized(record.value, record.unit)[0] - value) < 1e-6]
        if not exact:
            errors.append({"code": "FACT_NOT_FOUND",
                           "message": f"知识库中找不到 {claim.text} 对应的事实；不得编造参数",
                           "claim": claim.text, "span": [claim.start, claim.end]})
            continue
        hint = _field_hint(_clause(text, claim.start, claim.end))
        if hint:
            alias = _FIELD_ALIASES.get(hint, hint)
            hinted = [record for record in exact if alias in record.field]
            if hinted:
                exact = hinted
        record = exact[0]
        if record.qualifier in ("约", "大约") and record.qualifier not in claim.context:
            message = f"{claim.text} 来自“{record.raw_text}”，原文含“{record.qualifier}”，请保留限定"
            (errors if strict_qualifier else warnings).append(
                {"code": "QUALIFIER_DROPPED", "message": message,
                 "claim": claim.text, "span": [claim.start, claim.end],
                 "fact_id": record.fact_id})
        if record.conditions and not any(condition in claim.context for condition in record.conditions):
            warnings.append({"code": "CONDITION_MISSING",
                             "message": f"{claim.text} 的适用条件未写清：{'；'.join(record.conditions[:2])}",
                             "claim": claim.text, "span": [claim.start, claim.end],
                             "fact_id": record.fact_id})
    return {"passed": not errors, "errors": errors, "warnings": warnings,
            "checked": len(claims)}


def _clause_bounds(text: str, start: int, end: int) -> tuple[int, int]:
    separators = "，。；、,;\n"
    left = max((text.rfind(char, 0, start) for char in separators), default=-1) + 1
    rights = [position for position in (text.find(char, end) for char in separators)
              if position != -1]
    right = min(rights) if rights else len(text)
    return left, right


def _replacement_values(text: str, claim: Claim, records: list[FactRecord]) -> str:
    normalized = _normalized(claim.number, claim.unit)
    if normalized is None:
        return ""
    _, family = normalized
    candidates = [record for record in records
                  if _normalized(record.value, record.unit) and
                  _normalized(record.value, record.unit)[1] == family]
    hint = _field_hint(_clause(text, claim.start, claim.end))
    if hint:
        field = _FIELD_ALIASES.get(hint, hint)
        filtered = [record for record in candidates if field in record.field]
        if filtered:
            candidates = filtered
    values: list[str] = []
    for record in candidates[:4]:
        token = f"{record.value} {record.unit}"
        if token not in values:
            values.append(token)
    return " / ".join(values)


def sanitize_text(text: str, records: list[FactRecord]) -> str:
    """Replace unsupported numeric claims with real values for the same field.

    This is the last-resort repair after the model failed to fix its own copy.
    Only values that do not exist in the evidence are touched; if no real value
    exists, the containing clause is removed rather than left as a false claim.
    """
    report = check_text(text, records)
    errors = [item for item in report["errors"] if item.get("code") == "FACT_NOT_FOUND"]
    for error in sorted(errors, key=lambda item: item["span"][0], reverse=True):
        start, end = error["span"]
        claims = extract_claims(text[start:end])
        if not claims:
            continue
        claim = claims[0]
        absolute = Claim(text=claim.text, number=claim.number, unit=claim.unit,
                         start=start + claim.start, end=start + claim.end,
                         context=claim.context)
        replacement = _replacement_values(text, absolute, records)
        if replacement:
            text = text[:start] + replacement + text[end:]
        else:
            left, right = _clause_bounds(text, start, end)
            text = text[:left] + text[right:]
    return text


def sanitize_artifact(content: dict, records: list[FactRecord]) -> dict:
    """Apply :func:`sanitize_text` to every text surface of an artifact."""
    def walk(value):
        if isinstance(value, str):
            return sanitize_text(value, records)
        if isinstance(value, list):
            return [walk(item) for item in value]
        if isinstance(value, dict):
            return {key: (item if key in ("fact_ids", "image_id", "revision_hash", "engine")
                          else walk(item)) for key, item in value.items()}
        return value
    return walk(content)


def check_artifact(content: dict, records: list[FactRecord], *, strict_qualifier: bool = False) -> dict:
    """Check every text surface of an artifact (title/body/pages/tags)."""
    surfaces: list[tuple[str, str]] = []

    def walk(value, path: str):
        if isinstance(value, str):
            if value.strip():
                surfaces.append((path, value))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, f"{path}[{index}]")
        elif isinstance(value, dict):
            for key, item in value.items():
                if key in ("fact_ids", "image_id", "revision_hash", "engine"):
                    continue
                walk(item, f"{path}.{key}" if path else key)

    walk(content, "")
    errors: list[dict] = []
    warnings: list[dict] = []
    checked = 0
    for path, text in surfaces:
        report = check_text(text, records, strict_qualifier=strict_qualifier)
        checked += report["checked"]
        for item in report["errors"]:
            errors.append({**item, "path": path})
        for item in report["warnings"]:
            warnings.append({**item, "path": path})
    return {"passed": not errors, "errors": errors, "warnings": warnings,
            "checked": checked, "surfaces": len(surfaces)}
