"""Deterministic marketing-compliance guardrails for generated copy.

This performs pattern-based risk detection, not semantic fact verification.
It is specifically strict about product-parameter claims: any number in the
draft text must trace back to a number that actually appears in the cited
facts' source excerpts, otherwise it is treated as fabricated.
"""
import re

ABSOLUTE_CLAIMS = [
    "全网最低", "全网第一", "最好", "最强", "天花板", "吊打", "闭眼冲", "闭眼入",
    "秒杀所有", "绝对", "永久免费", "买它就对了", "无脑冲", "碾压", "完美无缺",
]
FAKE_EXPERIENCE = [
    r"亲测", r"实测(?:续航|成绩|性能|跑分)", r"用了\s*[一二三四五六七八九十\d]+\s*[天周月]",
    r"我连续用了", r"我已经用了", r"体验了一个月", r"实际使用\d+天",
]
UNVERIFIED_COMMERCIAL = [
    r"\d+\s*元", r"国补", r"补贴", r"优惠", r"折扣", r"赠品", r"秒杀", r"限时",
    r"库存紧张", r"仅剩\d+件", r"销量第一", r"好评率\d+%", r"获奖", r"认证第一",
]
COMPETITOR_PUT_DOWN = [r"友商.{0,6}(?:垫底|最差|不如|吊打)", r"别家.{0,6}(?:垃圾|不行)"]


def _numbers_in(text):
    return set(re.findall(r"\d+(?:\.\d+)?", text))


def audit_copy(title, body, tags, cta, facts, risks):
    """facts: list of {fact_id, text, conditions, source:{excerpt}}; risks from input_risks()."""
    errors, warnings = [], []
    full_text = "\n".join([title, body, cta, " ".join(tags)])

    for phrase in ABSOLUTE_CLAIMS:
        if phrase in full_text:
            errors.append(f"出现绝对化宣传用语：{phrase}")
    for pattern in FAKE_EXPERIENCE:
        if re.search(pattern, full_text):
            errors.append("出现无依据的亲测/实测经历表述")
            break
    for pattern in UNVERIFIED_COMMERCIAL:
        if re.search(pattern, full_text):
            errors.append("出现未经知识库核实的价格/促销/销量/荣誉类表述：" + pattern)
    for pattern in COMPETITOR_PUT_DOWN:
        if re.search(pattern, full_text):
            errors.append("出现无依据的竞品贬低表述")

    # Any digit sequence in the copy must be traceable to the cited facts' evidence.
    allowed_numbers = set()
    for fact in facts:
        excerpt = (fact.get("source") or {}).get("excerpt", "") if isinstance(fact, dict) else fact.source.excerpt
        text = fact.get("text", "") if isinstance(fact, dict) else fact.text
        allowed_numbers |= _numbers_in(excerpt) | _numbers_in(text)
    body_numbers = _numbers_in(title + " " + body)
    unknown_numbers = body_numbers - allowed_numbers
    if unknown_numbers:
        errors.append("文案包含知识库事实未支持的数字：" + "、".join(sorted(unknown_numbers)))

    # Risks flagged omit/omit_until_verified must not have leaked into the copy.
    for risk in risks or []:
        phrase = risk.get("request")
        if phrase and phrase in full_text:
            errors.append("文案包含应删除的高风险用语：" + phrase)

    if len(title) > 20:
        warnings.append("标题超过20字，建议精简")
    if not tags:
        warnings.append("未设置话题标签")

    return {
        "passed": not errors,
        "errors": errors,
        "warnings": warnings,
        "review_state": "passed_pending_manual_approval" if not errors else "blocked",
        "publish_ready": False,
        "note": "本检查为规则扫描，不是完整语义核验；发布前仍需人工复核。",
    }
