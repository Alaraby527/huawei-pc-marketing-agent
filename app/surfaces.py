"""Delivery surfaces, kept separate from platforms.

The three packages do not agree on channel vocabulary, and the user's decision was to keep
the existing ``Platform`` enum untouched and introduce a separate ``surface`` concept:

===========================  ==========================================
package 1                     ``xhs`` / ``work_account`` / ``moments`` / ``store_poster``
package 3                     ``xiaohongshu`` / ``personal_moments`` / ``wechat_article``
                              / ``store_moments`` / ``store_poster``
this project before          ``xiaohongshu`` / ``wechat_article`` / ``moments`` / ``poster``
===========================  ==========================================

Two real problems sit inside that table, and neither is solved by picking a spelling:

* this project's existing ``moments`` is labelled **门店朋友圈** in ``PLATFORM_RULES``, so it
  corresponds to package 3's ``store_moments``, while package 3's ``personal_moments`` is a
  genuinely new face;
* package 1's ``work_account`` has **no confirmed meaning**. That package says outright that
  it must not be assumed to be the official account and must not be silently renamed, so
  here it maps to ``platform = None`` and carries the question with it instead of guessing.

Specs are package 3's ``channel_specs.json``, which states its own limits are
"editorial_recommendations_not_platform_rules". Where those numbers disagree with this
project's older ``PLATFORM_RULES`` the disagreement is recorded in ``SPEC_CONFLICTS`` rather
than quietly overwritten: existing output must not change character lengths behind the
user's back.
"""
from __future__ import annotations

from .contracts import Platform

# surface_id -> spec. ``platform`` is the existing enum member, or None when the face has no
# confirmed platform behind it.
SURFACES: dict[str, dict] = {
    "xiaohongshu": {
        "label": "小红书图文", "platform": Platform.XIAOHONGSHU, "source": "package3",
        "width": 1080, "height": 1440, "default_pages": 6,
        "title_candidates": 5, "title_soft_max": 20,
        "body_soft_range": (350, 700), "card_count_range": (5, 7),
        "card_chars_soft_range": (35, 80),
        "extras": ("cover_brief", "tag_candidates"),
    },
    "personal_moments": {
        "label": "个人朋友圈", "platform": Platform.MOMENTS, "source": "package3",
        "width": 1080, "height": 1440, "default_pages": 1,
        "variants": {"daily": (60, 120), "value": (120, 220), "invitation": (80, 160)},
        "note": "日常型必须有真实事件；无事件时交替代稿并说明不可用原因，不得编造第一人称经历。",
    },
    "store_moments": {
        "label": "门店朋友圈", "platform": Platform.MOMENTS, "source": "package3",
        "width": 1080, "height": 1440, "default_pages": 1,
        "variants": {"service": (100, 200), "promotion_if_verified": (100, 200)},
        "chars_soft_range": (100, 200),
        "note": "活动型只在活动条件可核验时使用。",
    },
    "wechat_article": {
        "label": "公众号文章", "platform": Platform.WECHAT_ARTICLE, "source": "package3",
        "width": 1080, "height": 1440, "default_pages": 1,
        "title_candidates": 5, "outline_count": 1,
        "body_soft_range": (1000, 1800), "summary_soft_range": (60, 100),
        "image_briefs": 3,
    },
    "store_poster": {
        "label": "门店海报", "platform": Platform.POSTER, "source": "package3",
        "width": 1080, "height": 1440, "default_pages": 1,
        "concepts": ("benefit", "scene", "brand"),
        "headline_soft_max": 14, "subheadline_soft_max": 36,
        "required_fields": ("headline", "subheadline", "terms_if_applicable",
                            "primary_cta", "visual_brief", "rights_notes"),
    },
    "work_account": {
        "label": "工作号", "platform": None, "source": "package1",
        "width": 1080, "height": 1440, "default_pages": 1, "max_text_lines": 8,
        "role": "单图信息卡加正文",
        # Deliberately not mapped to wechat_article. Package 1 requires the real meaning to be
        # confirmed by the business first, and forbids substituting a different platform.
        "unconfirmed": True,
        "open_question": "工作号指工作微信、企业微信、公众号还是其他？确认前保持中性出口，"
                         "不得按公众号规格生成，也不得自行更名替换。",
    },
}

# The five hard gates from package 3's channel_specs.json.
HARD_GATES = ("verified_relevant_facts", "no_unresolved_placeholders", "valid_asset_rights",
              "valid_campaign_dates", "human_approval_for_exact_content")

LIMITS_DISCLAIMER = "editorial_recommendations_not_platform_rules"

# Numbers that disagree with this project's older PLATFORM_RULES. Recorded, not applied:
# silently shortening or lengthening existing output is a behaviour change nobody asked for.
SPEC_CONFLICTS = (
    {"surface": "xiaohongshu", "field": "body", "legacy": "300-600",
     "package3": "350-700", "resolution": "adopted_package3"},
    {"surface": "store_moments", "field": "chars", "legacy": "60-150（PLATFORM_RULES.moments）",
     "package3": "100-200", "resolution": "adopted_package3"},
    {"surface": "store_poster", "field": "headline", "legacy": "12 字内",
     "package3": "14 字内", "resolution": "adopted_package3"},
    {"surface": "wechat_article", "field": "body", "legacy": "600-1200",
     "package3": "1000-1800", "resolution": "adopted_package3"},
)

# 2026-10-22 裁定：4 处长度冲突一律采纳新方案（package3）值，旧规则作废。
# 运行时 4 处都已按新值生效，所以没有"并存待裁定"的项了。
SPEC_DECISION = {
    "decided_by": "项目负责人",
    "decided_at": "2026-10-22",
    "ruling": "4 处长度冲突一律采纳新方案（package3）值；旧规则作废。",
    # 用户原话："如果用户自己要求了字数则按用户要求为主"
    "override_rule": "用户在对话里明确要求的字数优先于本规格；规格是建议区间，不是平台规则。",
}

UNRESOLVED_CONFLICTS: tuple = ()


def surface_ids() -> list[str]:
    return list(SURFACES)


def spec(surface_id: str) -> dict | None:
    row = SURFACES.get(str(surface_id or "").strip())
    return dict(row) if row else None


def platform_for(surface_id: str):
    """The existing Platform behind a surface, or None when none is confirmed."""
    row = SURFACES.get(str(surface_id or "").strip())
    return row.get("platform") if row else None


def surfaces_for(platform) -> list[str]:
    try:
        wanted = Platform(platform)
    except ValueError:
        return []
    return [key for key, row in SURFACES.items() if row.get("platform") == wanted]


def resolve(value: str) -> dict:
    """Accept either a surface id or a platform value and return both, without guessing.

    ``work_account`` resolves to a surface with ``platform=None`` on purpose: the caller has
    to decide what to do about the unconfirmed meaning rather than inherit a wrong platform.
    """
    value = str(value or "").strip()
    if value in SURFACES:
        row = SURFACES[value]
        return {"surface": value, "platform": (row["platform"].value if row.get("platform") else None),
                "label": row["label"], "unconfirmed": bool(row.get("unconfirmed")),
                "open_question": row.get("open_question", ""), "spec": dict(row)}
    try:
        platform = Platform(value)
    except ValueError:
        return {"surface": "", "platform": None, "label": "", "unconfirmed": False,
                "open_question": "", "spec": None,
                "error": "未知交付面或平台：" + value}
    matches = surfaces_for(platform)
    return {"surface": matches[0] if len(matches) == 1 else "", "platform": platform.value,
            "label": SURFACES[matches[0]]["label"] if len(matches) == 1 else "",
            "unconfirmed": False, "open_question": "",
            "spec": dict(SURFACES[matches[0]]) if len(matches) == 1 else None,
            "ambiguous": matches if len(matches) > 1 else [],
            "note": ("该平台对应多个交付面（如个人/门店朋友圈），必须显式指定交付面"
                     if len(matches) > 1 else "")}


def limits_check(surface_id: str, *, body_chars: int | None = None,
                 headline_chars: int | None = None) -> dict:
    """Compare a draft against the soft limits, and say plainly that they are soft.

    A soft limit is reported as a note, never as a hard failure: package 3 itself labels
    these numbers editorial recommendations rather than platform rules.
    """
    row = spec(surface_id)
    if row is None:
        return {"ok": False, "error": "UNKNOWN_SURFACE", "surface": surface_id}
    notes = []
    if body_chars is not None and row.get("body_soft_range"):
        low, high = row["body_soft_range"]
        if body_chars < low:
            notes.append(f"正文 {body_chars} 字低于建议区间 {low}-{high}（建议值，非平台规则）")
        elif body_chars > high:
            notes.append(f"正文 {body_chars} 字高于建议区间 {low}-{high}（建议值，非平台规则）")
    # Two names for the same idea: a poster's 主标题 is headline_soft_max, a note's 标题 is
    # title_soft_max. Checking only one silently skipped the check for the other surface.
    if headline_chars is not None:
        cap = row.get("headline_soft_max") or row.get("title_soft_max")
        if cap and headline_chars > cap:
            label = "主标题" if row.get("headline_soft_max") else "标题"
            notes.append(f"{label} {headline_chars} 字超过建议上限 {cap} 字（建议值，非平台规则）")
    return {"ok": True, "surface": surface_id, "notes": notes,
            "limits_are": LIMITS_DISCLAIMER,
            "conflicts": [item for item in SPEC_CONFLICTS if item["surface"] == surface_id]}


def describe() -> dict:
    """Everything a client needs to render a surface picker, with the open question attached."""
    return {"surfaces": [{"surface": key, "label": row["label"],
                          "platform": row["platform"].value if row.get("platform") else None,
                          "unconfirmed": bool(row.get("unconfirmed")),
                          "open_question": row.get("open_question", "")}
                         for key, row in SURFACES.items()],
            "hard_gates": list(HARD_GATES),
            "limits_are": LIMITS_DISCLAIMER,
            "conflicts": list(SPEC_CONFLICTS),
            "note": "交付面与平台分离：work_account 的平台含义未经业务确认，故 platform 为空。"}
