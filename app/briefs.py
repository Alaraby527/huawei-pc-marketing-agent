"""Business inputs: read them, and refuse to write when a core one is missing or stale.

Package 3 puts the burden on the input side. Its controller prompt says to validate the
inputs first and to "缺核心输入先输出缺口；存在事实冲突标记 blocked", and its templates say
of themselves: "所有 null 为待补充；权限默认关闭；不把本模板直接当作已批准业务资料".

That last sentence matters most. The templates ship ``template_only: true`` with every value
null, and a reader that treats "field exists" as "field approved" would happily write copy for
an unbranded, unaudited, price-less product. So the checks here distinguish three outcomes
rather than two:

* **gap**    - a core input is absent; report what to supply and stop;
* **blocked** - the input exists but is stale, contradictory or unauthorised;
* **ok**     - usable now.

A price is a good example of why "blocked" is not "gap": an unknown price must never be written
as a number, and an expired price must never be reused, but they call for different actions.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

# Which fields are core per input, and what to tell the person who must supply them.
CORE_FIELDS = {
    "brand_profile": ("brand_name", "industry", "publishing_identity", "approval_owner"),
    "campaign_brief": ("primary_objective", "audience", "surfaces"),
    "product_catalog": ("product_ids",),
    "factbook": ("facts",),
    "proof_assets": ("assets",),
}

ALLOWED_OBJECTIVES = ("awareness", "saves", "qualified_inquiries", "appointments",
                      "store_visits", "sales")

# Facts whose validity window has passed, and prices with no recorded terms, are the two that
# most often leak into copy as if they were current.
FACT_FIELDS = ("field", "value", "source", "valid_from", "valid_until", "approved_by")


def _present(value) -> bool:
    if value is None or value is False:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, dict, set)):
        return len(value) > 0
    return True


def is_template(payload: dict) -> bool:
    """The package's own templates, which must not be read as approved business data.

    The templates announce themselves with ``template_only: true``, which is the primary
    signal. An earlier version also treated "no brand_id" as a template, and that wrongly
    swallowed factbook and proof_assets - neither of which has a brand_id at all - so their
    checks were skipped entirely. Only the explicit flag, or a payload that is entirely empty
    of values, counts.
    """
    if not isinstance(payload, dict):
        return False
    if payload.get("template_only"):
        return True
    substantive = [value for key, value in payload.items()
                   if key not in ("template_version", "template_only", "notes")]
    return not any(_present(value) for value in substantive)


def _as_date(value) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    text = str(value).strip().replace("/", "-")
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text[:len(fmt) + 2].strip(), fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def today() -> date:
    return datetime.now(timezone.utc).date()


def validate(kind: str, payload: dict, *, on_date: date | None = None) -> dict:
    """Check one input. Returns ok / gaps / blocked / warnings, never a silent pass."""
    kind = str(kind or "").strip()
    if kind not in CORE_FIELDS:
        return {"ok": False, "kind": kind, "error": "UNKNOWN_INPUT",
                "known": sorted(CORE_FIELDS)}
    if not isinstance(payload, dict):
        return {"ok": False, "kind": kind, "error": "NOT_AN_OBJECT",
                "gaps": [{"field": field, "why": "输入不是对象"}
                         for field in CORE_FIELDS[kind]]}
    gaps, blocked, warnings = [], [], []

    if is_template(payload):
        return {"ok": False, "kind": kind, "status": "template_not_approved",
                "gaps": [{"field": field, "why": "模板未填写"} for field in CORE_FIELDS[kind]],
                "blocked": [{"field": "template_only",
                             "why": "这是未填写的模板，方案明确不得当作已批准业务资料"}],
                "warnings": [], "note": "先补齐并取得业务批准，再用于写作。"}

    for field in CORE_FIELDS[kind]:
        if not _present(payload.get(field)):
            gaps.append({"field": field, "why": "核心输入缺失"})

    if kind == "brand_profile":
        identity = payload.get("publishing_identity") or {}
        if not _present(identity.get("role")):
            gaps.append({"field": "publishing_identity.role",
                         "why": "发布身份未定义；不得默认为品牌官方"})
        if not _present((payload.get("approval_owner") or {}).get("name")):
            gaps.append({"field": "approval_owner.name", "why": "没有审批人则无人可批准"})
        privacy = payload.get("privacy_rules") or {}
        if privacy.get("send_customer_data_to_external_models"):
            warnings.append({"field": "privacy_rules.send_customer_data_to_external_models",
                             "why": "品牌资料允许把客户数据发给外部模型；须确认合规依据"})
        if not _present(payload.get("forbidden_promises")):
            warnings.append({"field": "forbidden_promises",
                             "why": "未登记禁止承诺，写作时只能依赖通用规则"})

    if kind == "campaign_brief":
        objective = str(payload.get("primary_objective") or "")
        if objective and objective not in ALLOWED_OBJECTIVES:
            blocked.append({"field": "primary_objective",
                            "why": "目标不在允许集合内：" + objective})
        if len(payload.get("surfaces") or []) > 5:
            warnings.append({"field": "surfaces", "why": "交付面多于五种，请确认"})
        window = payload.get("campaign_window") or {}
        end = _as_date(window.get("valid_until") or payload.get("valid_until"))
        if end and end < (on_date or today()):
            blocked.append({"field": "valid_until",
                            "why": "活动已于 " + end.isoformat() + " 结束，不得再用于写作"})
        if payload.get("price_terms") in (None, "", "TBD", "待定"):
            # Not a gap to fill silently later: writing an unverified price is the failure mode.
            blocked.append({"field": "price_terms",
                            "why": "价格条款未知或为占位；不得写入任何数字"})

    if kind == "factbook":
        for index, fact in enumerate(payload.get("facts") or []):
            if not isinstance(fact, dict):
                blocked.append({"field": f"facts[{index}]", "why": "事实条目不是对象"})
                continue
            for field in ("field", "value", "source"):
                if not _present(fact.get(field)):
                    gaps.append({"field": f"facts[{index}].{field}",
                                 "why": "事实缺少字段或来源，无法核实"})
            until = _as_date(fact.get("valid_until"))
            if until and until < (on_date or today()):
                blocked.append({"field": f"facts[{index}].valid_until",
                                "why": str(fact.get("field") or "") + " 已于 "
                                       + until.isoformat() + " 失效"})
            if not _present(fact.get("approved_by")):
                warnings.append({"field": f"facts[{index}].approved_by",
                                 "why": "事实未记录批准人"})
        conflicts = fact_conflicts(payload.get("facts") or [])
        for conflict in conflicts:
            blocked.append({"field": conflict["field"],
                            "why": "同一字段存在互相矛盾的取值：" + " / ".join(conflict["values"])})

    if kind == "proof_assets":
        for index, asset in enumerate(payload.get("assets") or []):
            if not isinstance(asset, dict):
                blocked.append({"field": f"assets[{index}]", "why": "素材条目不是对象"})
                continue
            missing = [field for field in ("owner", "consent_record",
                                           "license_or_authorization_ref")
                       if not _present(asset.get(field))]
            if missing:
                blocked.append({"field": f"assets[{index}]",
                                "why": "素材授权不完整，缺少 " + "、".join(missing)})
            if not _present(asset.get("allowed_channels")):
                blocked.append({"field": f"assets[{index}].allowed_channels",
                                "why": "未登记允许渠道；不得默认全渠道可用"})
            until = _as_date(asset.get("valid_until"))
            if until and until < (on_date or today()):
                blocked.append({"field": f"assets[{index}].valid_until",
                                "why": "素材授权已过期"})

    status = "blocked" if blocked else ("needs_input" if gaps else "ok")
    return {"ok": status == "ok", "kind": kind, "status": status, "gaps": gaps,
            "blocked": blocked, "warnings": warnings,
            "note": ("核心输入缺口需业务补齐。" if gaps else "")
                    + ("存在阻断项，按方案标记 blocked。" if blocked else "")}


def fact_conflicts(facts: list) -> list[dict]:
    """Same field, different values. A contradiction is blocked, not averaged."""
    seen: dict[str, set] = {}
    for fact in facts or []:
        if not isinstance(fact, dict):
            continue
        field = str(fact.get("field") or "").strip()
        value = str(fact.get("value") or "").strip()
        if not field or not value:
            continue
        seen.setdefault(field, set()).add(value)
    return [{"field": field, "values": sorted(values)}
            for field, values in sorted(seen.items()) if len(values) > 1]


def price_or_block(factbook: dict, field: str = "价格") -> dict:
    """A price either has a verified value with a source, or it blocks the copy."""
    for fact in (factbook or {}).get("facts") or []:
        if not isinstance(fact, dict) or str(fact.get("field") or "") != field:
            continue
        value = str(fact.get("value") or "").strip()
        if not value or value in ("TBD", "待定", "unknown"):
            continue
        return {"ok": True, "value": value, "source": fact.get("source"),
                "valid_until": fact.get("valid_until"),
                "note": "价格已核实；仍需保留适用条件与有效期"}
    return {"ok": False, "error": "PRICE_UNVERIFIED",
            "message": "没有可核实的价格事实：不得写入任何价格数字或折扣"}


def publishable_blockers(bundle: dict) -> list[dict]:
    """Everything that must be resolved before a bundle may leave as a publishable pack.

    Package 3's hard gates, plus the placeholder rule, in one place so a caller cannot pick
    only the convenient ones.
    """
    problems = []
    for kind in ("brand_profile", "campaign_brief", "factbook", "proof_assets"):
        payload = (bundle or {}).get(kind)
        if payload is None:
            problems.append({"gate": "verified_relevant_facts", "input": kind,
                             "why": "缺少 " + kind})
            continue
        result = validate(kind, payload)
        for row in result.get("blocked") or []:
            problems.append({"gate": "verified_relevant_facts", "input": kind,
                             "field": row["field"], "why": row["why"]})
        for row in result.get("gaps") or []:
            problems.append({"gate": "verified_relevant_facts", "input": kind,
                             "field": row["field"], "why": row["why"]})
    text = str((bundle or {}).get("copy_text") or "")
    if text:
        from .runtime.content_policy import unresolved_placeholders
        found = unresolved_placeholders(text)
        if found:
            problems.append({"gate": "no_unresolved_placeholders", "field": "copy_text",
                             "why": "存在占位内容：" + "、".join(found[:5])})
    if (bundle or {}).get("approved") is True:
        approver = str((bundle or {}).get("approved_by") or "")
        if not approver:
            problems.append({"gate": "human_approval_for_exact_content", "field": "approved_by",
                             "why": "标记为已批准但没有任何批准人"})
    return problems


def status() -> dict:
    return {"core_fields": {kind: list(fields) for kind, fields in CORE_FIELDS.items()},
            "allowed_objectives": list(ALLOWED_OBJECTIVES),
            "note": "模板 template_only=true 一律视为未批准；缺核心输入报缺口，"
                    "冲突、过期、无授权则报 blocked。"}