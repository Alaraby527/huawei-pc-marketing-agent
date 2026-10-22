"""Method catalogue from package 3: 20 writing methods, routed by surface, objective and evidence.

The catalogue's value is not the list of names but the two columns that constrain use:
``required_evidence`` (what must exist before the method may be used at all) and
``do_not_use_when`` (the condition that disqualifies it). Both are conditions, not advice, so
``route`` returns the methods that are *allowed* given the available evidence and reports the
ones it excluded **with the reason** - a silent fallback to a generic method is how a system
ends up writing a fabricated customer case.

Package 3 also warns that a confirmation gate applies to strategy: its controller stops for a
human only for a new brand's first three times, or when price or a major promise changes. That
policy lives in ``needs_strategy_confirmation`` so the caller cannot forget it.
"""
from __future__ import annotations

from .surfaces import SURFACES

# method_id -> row. Transcribed from package 3's templates/method_catalog.csv.
METHODS: dict[str, dict] = {
    "X01": {"surfaces": ("xiaohongshu",), "name": "搜索答疑", "objective": "搜索需求、咨询",
            "required_evidence": "已批准产品事实",
            "structure": "问题→结论→标准→适用人群→行动",
            "do_not_use_when": "问题超出产品能力", "routing_condition": "选购或使用问题"},
    "X02": {"surfaces": ("xiaohongshu",), "name": "场景痛点", "objective": "种草、咨询",
            "required_evidence": "场景与产品能力对应",
            "structure": "场景→麻烦→办法→产品作用→边界",
            "do_not_use_when": "场景只能靠编造经历", "routing_condition": "人群明确"},
    "X03": {"surfaces": ("xiaohongshu",), "name": "对比决策", "objective": "选择、咨询",
            "required_evidence": "同口径且有来源的比较",
            "structure": "任务→维度→差别→人群建议→限制",
            "do_not_use_when": "比较数据不可核实", "routing_condition": "两种或以上方案"},
    "X04": {"surfaces": ("xiaohongshu",), "name": "真实案例", "objective": "信任",
            "required_evidence": "真实性及对外授权",
            "structure": "背景→需求→方案→过程→反馈→限制",
            "do_not_use_when": "没有真实授权案例", "routing_condition": "案例材料齐全"},
    "X05": {"surfaces": ("xiaohongshu",), "name": "清单攻略", "objective": "收藏",
            "required_evidence": "可解释的信息清单",
            "structure": "适用人群→步骤→误区→建议",
            "do_not_use_when": "清单只是宣传口号", "routing_condition": "实用信息足够"},
    "M01": {"surfaces": ("personal_moments",), "name": "日常观察", "objective": "认知",
            "required_evidence": "可确认的本人事件",
            "structure": "小事→观察→业务关联",
            "do_not_use_when": "无真实事件却写第一人称", "routing_condition": "发布者记录齐全"},
    "M02": {"surfaces": ("personal_moments",), "name": "常见问答", "objective": "咨询",
            "required_evidence": "有依据的回答",
            "structure": "问题→回答→理由→条件",
            "do_not_use_when": "虚构客户原话", "routing_condition": "可标明为常见问题"},
    "M03": {"surfaces": ("personal_moments",), "name": "服务过程", "objective": "信任",
            "required_evidence": "服务流程或标准",
            "structure": "做了什么→为什么→价值→下一步",
            "do_not_use_when": "把服务设想写成已发生", "routing_condition": "流程可兑现"},
    "M04": {"surfaces": ("personal_moments",), "name": "客户案例", "objective": "信任",
            "required_evidence": "授权案例及反馈",
            "structure": "需求→方案→反馈→适用人群",
            "do_not_use_when": "伪造聊天或评价", "routing_condition": "有可用案例"},
    "M05": {"surfaces": ("personal_moments",), "name": "明确邀约", "objective": "预约",
            "required_evidence": "服务权益和条件",
            "structure": "适合谁→利益→条件→行动",
            "do_not_use_when": "权益不可兑现", "routing_condition": "行动路径真实"},
    "W01": {"surfaces": ("wechat_article",), "name": "问题解决", "objective": "专业认知",
            "required_evidence": "可靠解释与产品资料",
            "structure": "问题→成因→方案→应用→边界→行动",
            "do_not_use_when": "越界作专业保证", "routing_condition": "复杂问题"},
    "W02": {"surfaces": ("wechat_article",), "name": "案例拆解", "objective": "信任",
            "required_evidence": "完整授权案例",
            "structure": "背景→难点→选择→实施→结果→限制",
            "do_not_use_when": "无法核实结果", "routing_condition": "交付案例齐全"},
    "W03": {"surfaces": ("wechat_article",), "name": "选购指南", "objective": "选择",
            "required_evidence": "同口径产品资料",
            "structure": "任务→标准→差别→误区→建议",
            "do_not_use_when": "缺比较依据仍下排名", "routing_condition": "选购任务"},
    "W04": {"surfaces": ("wechat_article",), "name": "品牌故事", "objective": "品牌认知",
            "required_evidence": "真实事件和使用权",
            "structure": "事件→取舍→实践→客户意义",
            "do_not_use_when": "虚构创始故事", "routing_condition": "有经营素材"},
    "W05": {"surfaces": ("wechat_article",), "name": "活动专题", "objective": "预约、到店",
            "required_evidence": "活动对象权益期限条件",
            "structure": "原因→对象→内容→价值→规则→参与",
            "do_not_use_when": "关键活动条件缺失", "routing_condition": "完整活动 brief"},
    "S01": {"surfaces": ("store_moments", "store_poster"), "name": "利益直给", "objective": "到店",
            "required_evidence": "价格权益期限及门店",
            "structure": "利益→对象→条件→行动",
            "do_not_use_when": "虚构原价或稀缺", "routing_condition": "真实活动"},
    "S02": {"surfaces": ("store_moments", "store_poster"), "name": "场景需求", "objective": "推荐",
            "required_evidence": "可兑现能力",
            "structure": "场景→需求→方案→适合谁→行动",
            "do_not_use_when": "无依据功效暗示", "routing_condition": "日常业务"},
    "S03": {"surfaces": ("store_moments", "store_poster"), "name": "新品解释", "objective": "新品体验",
            "required_evidence": "上架和新品资料",
            "structure": "新在哪里→作用→适合谁→体验",
            "do_not_use_when": "未上市却称已到店", "routing_condition": "新品确认"},
    "S04": {"surfaces": ("store_moments", "store_poster"), "name": "节日创意", "objective": "品牌认知",
            "required_evidence": "关联性和素材权利",
            "structure": "节日关联→短句→业务解释→信息",
            "do_not_use_when": "照搬他人品牌海报", "routing_condition": "有适当节日场景"},
    "S05": {"surfaces": ("store_moments", "store_poster"), "name": "顾虑说明", "objective": "咨询预约",
            "required_evidence": "服务政策和限制",
            "structure": "疑问→回答→条件→行动",
            "do_not_use_when": "绝对保证或捏造售后", "routing_condition": "存在具体顾虑"},
}

# Evidence a surface's methods may require. Kept as a closed set so a caller cannot claim an
# evidence type the system has no way to verify.
EVIDENCE_KINDS = ("verified_relevant_facts", "scene_fit", "comparable_data", "real_case",
                  "usable_list", "own_event", "answerable_question", "service_process",
                  "service_terms", "explanation", "brand_material", "campaign_terms",
                  "price_terms", "product_capability", "launch_info", "festival_material",
                  "service_policy")

# Which evidence kinds can actually be satisfied by something this project holds.
EVIDENCE_VERIFIABLE = {
    "verified_relevant_facts": True,     # knowledge base facts, already verified
    "scene_fit": True,                   # derived from verified facts
    "comparable_data": True,             # cross-model field matrices
    "usable_list": True,                 # derived from verified facts
    "explanation": True,                 # derived from verified facts
    "product_capability": True,
    "answerable_question": True,
    # Everything below needs a human or a business system: prices, campaigns, cases,
    # authorisations, service policy. The codes cannot invent them.
    "real_case": False,
    "own_event": False,
    "service_process": False,
    "service_terms": False,
    "brand_material": False,
    "campaign_terms": False,
    "price_terms": False,
    "launch_info": False,
    "festival_material": False,
    "service_policy": False,
}

# Maps a method's free-text required evidence onto a checkable kind.
_EVIDENCE_MAP = {
    "已批准产品事实": "verified_relevant_facts",
    "场景与产品能力对应": "scene_fit",
    "同口径且有来源的比较": "comparable_data",
    "真实性及对外授权": "real_case",
    "可解释的信息清单": "usable_list",
    "可确认的本人事件": "own_event",
    "有依据的回答": "answerable_question",
    "服务流程或标准": "service_process",
    "授权案例及反馈": "real_case",
    "服务权益和条件": "service_terms",
    "可靠解释与产品资料": "explanation",
    "完整授权案例": "real_case",
    "同口径产品资料": "comparable_data",
    "真实事件和使用权": "brand_material",
    "活动对象权益期限条件": "campaign_terms",
    "价格权益期限及门店": "price_terms",
    "可兑现能力": "product_capability",
    "上架和新品资料": "launch_info",
    "关联性和素材权利": "festival_material",
    "服务政策和限制": "service_policy",
}


def evidence_kind(method_id: str) -> str:
    row = METHODS.get(method_id)
    if not row:
        return ""
    return _EVIDENCE_MAP.get(row["required_evidence"], "")


def methods_for(surface_id: str) -> list[str]:
    return [key for key, row in METHODS.items() if surface_id in row["surfaces"]]


def route(surface_id: str, *, available_evidence=(), objective: str = "") -> dict:
    """Methods this surface may use, plus the excluded ones and why they were excluded.

    An excluded method is reported with ``blocked_by`` naming the missing evidence. Returning
    it silently would let the caller write a case study with no case.
    """
    candidates = methods_for(surface_id)
    if not candidates:
        return {"surface": surface_id, "allowed": [], "excluded": [], "methods": [],
                "error": "UNKNOWN_SURFACE",
                "known_surfaces": [key for key in SURFACES]}
    have = set(available_evidence or ())
    allowed, excluded = [], []
    for method_id in candidates:
        row = METHODS[method_id]
        kind = evidence_kind(method_id)
        if kind and kind in have:
            allowed.append(method_id)
            continue
        excluded.append({"method_id": method_id, "name": row["name"],
                         "blocked_by": kind or row["required_evidence"],
                         "verified_available": EVIDENCE_VERIFIABLE.get(kind),
                         "do_not_use_when": row["do_not_use_when"],
                         "reason": ("需要「" + row["required_evidence"] + "」，当前不满足；"
                                    + ("该证据本系统无法自造，须由业务提供"
                                       if EVIDENCE_VERIFIABLE.get(kind) is False
                                       else "需先取得该证据"))})
    if objective:
        wanted = objective.strip()
        allowed.sort(key=lambda mid: 0 if wanted in METHODS[mid]["objective"] else 1)
    return {"surface": surface_id, "allowed": allowed, "excluded": excluded,
            "methods": [{"method_id": key, **METHODS[key]} for key in allowed],
            "note": "被排除的方法必须连同原因一起呈现，不得静默换成通用写法。"}


def describe() -> dict:
    return {"count": len(METHODS),
            "by_surface": {surface: methods_for(surface) for surface in SURFACES},
            "evidence_verifiable": EVIDENCE_VERIFIABLE,
            "note": "价格、活动、案例、授权、服务政策类证据代码无法自造，必须由业务提供。"}


def needs_strategy_confirmation(*, brand_runs: int, price_changed: bool = False,
                                major_promise_changed: bool = False) -> dict:
    """Package 3's confirmation gate: stop for a human only in these cases.

    Requiring confirmation every time would make the tool unusable; never requiring it is how
    a brand's first three posts go out with an unapproved tone.
    """
    if brand_runs < 3:
        return {"required": True, "reason": "new_brand_first_three",
                "message": "这是该品牌的前 3 次创作，策略需人工确认后再写。"}
    if price_changed or major_promise_changed:
        return {"required": True,
                "reason": "price_changed" if price_changed else "major_promise_changed",
                "message": "价格或重大承诺发生变化，需人工确认后再写。"}
    return {"required": False, "reason": "", "message": ""}