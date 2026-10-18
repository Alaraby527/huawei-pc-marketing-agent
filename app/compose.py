"""Platform copy composition: turns a handoff (facts + verified images) into
a draft (title/body/tags/cta/image plan) for the confirmed platform.

Two engines:
  - "rule": deterministic local template, zero model calls, always available.
  - "llm" : calls the configured OpenAI-Next-compatible text model; the
            model may only cite fact_id values that exist in the handoff,
            and any number it writes is re-checked against evidence by
            app.compliance before the draft is shown to the operator.
"""
import re
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from .model import complete_json, ModelError
from .platforms import PLATFORM_RULES

SYSTEM_PROMPT = """你是华为PC门店营销内容协作者。只能使用用户消息里 facts 数组中的事实，
每条可核查的表述都要在 fact_ids 中标注对应 fact_id；禁止编写 facts 之外的参数、价格、促销、
销量、奖项或个人体验。禁止绝对化宣传（例如“全网最低”“吊打”“闭眼入”）。字数、图数和结构必须
符合 platform_rules。按平台输出 JSON，字段为：
{"title":"","body":"","tags":["..."],"cta":"","fact_ids":["F-xxx"],
 "image_plan":[{"image_id":"I-xxx","position":"封面/第1张","reason":""}]}
不要输出 Markdown 代码围栏，不要输出除 JSON 以外的任何文字。"""


class _LLMDraft(BaseModel):
    """The model is untrusted; never index into its JSON until it passes this."""
    model_config = ConfigDict(extra='ignore')
    title: str = Field(min_length=1, max_length=120)
    body: str = Field(min_length=1, max_length=5000)
    tags: list[str] = Field(default_factory=list, max_length=20)
    cta: str = Field(default='', max_length=300)
    fact_ids: list[str] = Field(default_factory=list, max_length=50)
    image_plan: list[dict] = Field(default_factory=list, max_length=20)


def _rule_fallback(task, facts, images, strategy, risks, reason=''):
    return _rule_based(task, facts, images, strategy, risks), {
        "provider": "rule-fallback", "model": None, "reason": reason}


def _rule_based(task, facts, images, strategy, risks):
    rule = PLATFORM_RULES[task.platform.value]
    priority_ids = [f.fact_id for f in facts[:3]]
    highlight = "、".join(f.text.partition("：")[0] for f in facts[:3]) or "核心配置"
    scene_label = re.sub(r"[，。；、\s]+", "", task.scenario)[:8] or "日常使用"
    scene = strategy.get("angle", "日常使用场景")
    title = f"{strategy.get('audience','这类人群')}选PC，先看这几点"[:20]
    body_lines = [
        f"{scene}，不少朋友第一反应是看参数表，其实更该先想清楚自己的真实需求。",
        f"这次整理的重点是 {highlight}，都来自官方资料，没有的我们不编。",
    ]
    for fact in facts[:3]:
        label, _, detail = fact.text.partition("：")
        detail = re.sub(r"\s*\n+\s*", " ", detail.strip())[:60]
        body_lines.append(f"【{label}】{detail}（具体以实际销售配置为准）")
    body_lines.append(strategy.get("user_problem", "具体是否适合你，建议结合实际使用场景确认。"))
    body = "\n\n".join(body_lines)[:rule.get("max_chars", 600) if "max_chars" in rule else 600]
    tags = list(dict.fromkeys([strategy.get("audience", "")[:8], scene_label, "华为PC", "数码好物"]))
    tags = [t for t in tags if t][:6]
    image_plan = [{"image_id": img.image_id, "position": "封面" if i == 0 else f"第{i}张",
                    "reason": "与当前产品型号一致，用于说明对应卖点"} for i, img in enumerate(images[:rule["image_max"]])]
    return {"title": title, "body": body, "tags": tags, "cta": rule["cta"],
            "fact_ids": priority_ids, "image_plan": image_plan}


def _llm_based(task, facts, images, strategy, risks):
    rule = PLATFORM_RULES[task.platform.value]
    payload = {
        "platform": task.platform.value, "platform_rules": rule, "strategy": strategy,
        "forbidden_claims": strategy.get("forbidden_claims", []),
        "facts": [{"fact_id": f.fact_id, "text": f.text, "conditions": f.conditions} for f in facts],
        "images": [{"image_id": i.image_id, "caption": i.caption} for i in images],
        "input_risks": risks,
    }
    schema_hint = "fact_ids 和 image_plan[].image_id 只能引用上面给出的 facts/images 中的 id。"
    try:
        result, meta = complete_json(SYSTEM_PROMPT, payload, schema_hint=schema_hint)
    except ModelError:
        return _rule_fallback(task, facts, images, strategy, risks, 'model_error')
    if not isinstance(result, dict):
        return _rule_fallback(task, facts, images, strategy, risks, 'non_object')
    try:
        parsed = _LLMDraft.model_validate(result)
    except ValidationError:
        return _rule_fallback(task, facts, images, strategy, risks, 'invalid_shape')
    result = parsed.model_dump()
    allowed_fact_ids = {f.fact_id for f in facts}
    allowed_image_ids = {i.image_id for i in images}
    result["fact_ids"] = [x for x in result["fact_ids"] if x in allowed_fact_ids]
    result["image_plan"] = [p for p in result["image_plan"]
                            if isinstance(p.get("image_id"), str) and p["image_id"] in allowed_image_ids]
    if not result["title"].strip() or not result["body"].strip():
        return _rule_fallback(task, facts, images, strategy, risks, 'empty_copy')
    result["tags"] = [t.strip() for t in result["tags"] if t.strip()][:6]
    if not result["cta"].strip():
        result["cta"] = rule["cta"]
    return result, meta


def compose_draft(task, facts, images, strategy, risks, engine="rule"):
    if engine == "llm":
        draft, meta = _llm_based(task, facts, images, strategy, risks)
    else:
        draft = _rule_based(task, facts, images, strategy, risks)
        meta = {"provider": "local-rule-template", "model": None}
    rule = PLATFORM_RULES[task.platform.value]
    draft["platform"] = task.platform.value
    draft["platform_name"] = rule["name"]
    draft["engine_meta"] = meta
    return draft


def revise_draft(draft, instruction, facts, images, strategy, risks, task, engine="rule"):
    """Apply a bounded set of deterministic edits; never invent new facts."""
    text = instruction.strip()
    new_draft = dict(draft)
    if re.search(r"短|精简|缩短", text):
        paragraphs = new_draft["body"].split("\n\n")
        new_draft["body"] = "\n\n".join(paragraphs[:max(2, len(paragraphs) - 1)])
    if re.search(r"口语|轻松|活泼", text):
        new_draft["title"] = new_draft["title"].rstrip("。").rstrip("！") + "，真的绕不开这几点"
        new_draft["title"] = new_draft["title"][:20]
    if re.search(r"删.{0,4}卖点|去掉.{0,4}(?:卖点|这条|这点)", text):
        new_draft["fact_ids"] = new_draft.get("fact_ids", [])[:-1]
    if re.search(r"正式|专业|克制", text):
        new_draft["title"] = re.sub(r"[！!]+$", "", new_draft["title"])
    if re.search(r"换(?:人群|受众)", text):
        return compose_draft(task, facts, images, strategy, risks, engine=engine)
    return new_draft
