"""Platform copy composition: turns a handoff (facts + verified images) into
a draft (title/body/tags/cta/six-page plan) for the confirmed platform.

Two engines:
  - "rule": deterministic local template, zero model calls, always available.
  - "llm" : calls the configured OpenAI-compatible text model; the model may
            only cite fact_id values that exist in the handoff, and every
            page is structurally validated before it is shown to the operator.
"""
import re
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from .contracts import PAGE_ROLES
from .model import complete_json, ModelError
from .platforms import PLATFORM_RULES

SYSTEM_PROMPT = """你是华为PC门店营销内容协作者。交付物是有知识库依据的小红书图文方案。
只能使用用户消息里 facts 数组中的事实；禁止编写 facts 之外的参数、价格、促销、销量、奖项、
排名或个人体验。热点只是切入线索，不是产品事实。禁止绝对化宣传（例如“全网最低”“吊打”“闭眼入”）。
不要写“实测数据”“实测结果”“亲测”或任何没有记录支撑的体验结论。文案里的每个数字（包括小数、比例、单位）都必须逐字来自 facts，
不要四舍五入、不要改写尺寸/重量/比例，不确定就不写数字。字数、图数和结构必须符合 platform_rules。

按小红书图文组织六页，角色固定且顺序固定：
hook（封面反差）→ scene（真实场景）→ proof（有依据的事实）→ detail（细节/条件）→ decision（适合谁）→ close（讨论/行动）。
六页中至少两页引用事实；每页 fact_ids 最多两个；image_id 只能来自给定图片。
标题不超过20字，正文建议180–650字，标签3–6个；单页标题不超过24字，单页正文不超过180字。

只输出一个 JSON 对象，不要 Markdown 代码围栏，不要输出 JSON 以外的文字。字段为：
{"title":"","body":"","tags":["..."],"cta":"","fact_ids":["F-xxx"],
 "pages":[{"role":"hook","title":"","text":"","fact_ids":[],"image_id":"I-xxx"}],
 "image_plan":[{"image_id":"I-xxx","position":"封面/第1张","reason":""}]}
pages 必须正好六项，role 必须按上面的顺序。"""


class _LLMPage(BaseModel):
    model_config = ConfigDict(extra='ignore')
    role: str = Field(min_length=1, max_length=20)
    title: str = Field(min_length=1, max_length=60)
    text: str = Field(min_length=1, max_length=400)
    fact_ids: list[str] = Field(default_factory=list, max_length=3)
    image_id: str = Field(default='', max_length=100)


class _LLMDraft(BaseModel):
    """The model is untrusted; never index into its JSON until it passes this."""
    model_config = ConfigDict(extra='ignore')
    title: str = Field(min_length=1, max_length=120)
    body: str = Field(min_length=1, max_length=5000)
    tags: list[str] = Field(default_factory=list, max_length=20)
    cta: str = Field(default='', max_length=300)
    fact_ids: list[str] = Field(default_factory=list, max_length=50)
    pages: list[_LLMPage] = Field(default_factory=list, max_length=8)
    image_plan: list[dict] = Field(default_factory=list, max_length=20)


def _rule_fallback(task, facts, images, strategy, risks, reason=''):
    return _rule_based(task, facts, images, strategy, risks), {
        "provider": "rule-fallback", "model": None, "reason": reason}


def _short_detail(text, limit=80):
    """Cut at punctuation/space instead of chopping a number or unit in half."""
    text = re.sub(r"\s+", " ", str(text).strip())
    if len(text) <= limit:
        return text
    cut = text[:limit]
    for separator in ("。", "；", "，", " "):
        index = cut.rfind(separator)
        if index >= 20:
            return cut[:index + 1].rstrip("，") + "…"
    return cut + "…"


def _scene_tag(scenario):
    routes = [
        (["学习", "论文", "网课", "课堂", "校园"], "校园学习"),
        (["办公", "会议", "差旅", "出差", "文档"], "移动办公"),
        (["创作", "剪辑", "设计", "摄影"], "内容创作"),
        (["家庭", "换新", "父母"], "家庭换新"),
        (["企业", "采购", "部署"], "企业采购"),
    ]
    return next((label for words, label in routes if any(word in scenario for word in words)), "数码生活")


def _rule_pages(task, facts, images, strategy):
    scene = re.sub(r"[，。；、\s]+", "", task.scenario)[:18] or "日常使用"
    scene_tag = _scene_tag(task.scenario)
    facts = facts[:3]

    def fact(index):
        if index < len(facts):
            fact_item = facts[index]
            label, _, detail = fact_item.text.partition("：")
            return f"{label}：{_short_detail(detail, 60)}", [fact_item.fact_id]
        if facts:
            return f"{facts[0].text.partition('：')[0]}的具体条件请以销售版本为准。", []
        return "具体配置和适用条件请以销售版本为准。", []

    first_text, first_ids = fact(0)
    second_text, second_ids = fact(1)
    third_text, third_ids = fact(2)
    if not second_ids and first_ids:
        second_text, second_ids = first_text, first_ids
    if not third_ids:
        third_text, third_ids = second_text, second_ids
    image_ids = [image.image_id for image in images]

    def image(index):
        return image_ids[index % len(image_ids)] if image_ids else ""

    return [
        {"role": "hook", "title": f"{scene_tag}选本，先别急着看参数"[:24],
         "text": f"先从{scene}的真实任务出发，把需求排在配置表前面。",
         "fact_ids": [], "image_id": image(0)},
        {"role": "scene", "title": "先把一天的任务排清楚",
         "text": f"上课、办公、创作还是家庭使用？把{scene}里每天真正会做的事写下来。",
         "fact_ids": [], "image_id": image(1)},
        {"role": "proof", "title": "这一页看有依据的规格",
         "text": first_text, "fact_ids": first_ids, "image_id": image(2)},
        {"role": "detail", "title": "细节要和你的用法对上",
         "text": second_text, "fact_ids": second_ids, "image_id": image(3)},
        {"role": "decision", "title": "把“适合我”说具体",
         "text": third_text, "fact_ids": third_ids, "image_id": image(4)},
        {"role": "close", "title": "最后一个问题留给你",
         "text": strategy.get("user_problem", "你选电脑时最想先解决哪个问题？"),
         "fact_ids": [], "image_id": image(5)},
    ]


def _rule_based(task, facts, images, strategy, risks):
    rule = PLATFORM_RULES[task.platform.value]
    priority_ids = [f.fact_id for f in facts[:3]]
    highlight = "、".join(f.text.partition("：")[0] for f in facts[:3]) or "核心配置"
    scene_tag = _scene_tag(task.scenario)
    scene = strategy.get("angle", "日常使用场景")
    hotspot = (task.hotspot or "").strip()
    opening = (f"最近看到「{hotspot[:28]}」这个话题，我更想把选电脑这件事落回真实使用场景。"
               if hotspot else "选电脑时，不少朋友第一反应是看参数表，其实更该先想清楚自己的真实需求。")
    title = f"{scene_tag}选本，先看这几点"[:20]
    body_lines = [
        opening,
        f"这次先看 {highlight}，都来自知识库原文。",
    ]
    for fact in facts[:3]:
        label, _, detail = fact.text.partition("：")
        body_lines.append(f"【{label}】{_short_detail(detail, 70)}")
    body_lines.append("不同销售版本的配置和条件不同，下单前请核对具体版本。")
    body_lines.append(strategy.get("user_problem", "具体是否适合你，建议结合实际使用场景确认。"))
    body = "\n\n".join(body_lines)[:600]
    tags = list(dict.fromkeys([strategy.get("audience", "")[:8], scene_tag, "华为PC", "数码好物"]))
    tags = [t for t in tags if t][:6]
    image_plan = [{"image_id": img.image_id, "position": "封面" if i == 0 else f"第{i}张",
                    "reason": "与当前产品型号一致，用于说明对应卖点"} for i, img in enumerate(images[:rule["image_max"]])]
    return {"title": title, "body": body, "tags": tags, "cta": rule["cta"],
            "fact_ids": priority_ids, "pages": _rule_pages(task, facts, images, strategy),
            "image_plan": image_plan}


def _llm_based(task, facts, images, strategy, risks, model=None):
    rule = PLATFORM_RULES[task.platform.value]
    payload = {
        "platform": task.platform.value, "platform_rules": rule, "strategy": strategy,
        "hotspot": task.hotspot,
        "forbidden_claims": strategy.get("forbidden_claims", []),
        "facts": [{"fact_id": f.fact_id, "text": f.text, "conditions": f.conditions} for f in facts],
        "images": [{"image_id": i.image_id, "caption": i.caption} for i in images],
        "input_risks": risks,
    }
    schema_hint = "fact_ids 和 pages[].fact_ids 只能引用上面 facts 中的 id；pages[].image_id 和 image_plan[].image_id 只能引用上面 images 中的 id。"
    try:
        result, meta = complete_json(SYSTEM_PROMPT, payload, schema_hint=schema_hint, model=model)
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
    pages = []
    for index, page in enumerate(result["pages"]):
        page["fact_ids"] = [x for x in page["fact_ids"] if x in allowed_fact_ids][:2]
        if page["image_id"] not in allowed_image_ids:
            page["image_id"] = images[index % len(images)].image_id if images else ''
        pages.append({key: page[key] for key in ("role", "title", "text", "fact_ids", "image_id")})
    if [p["role"] for p in pages] != list(PAGE_ROLES) or sum(bool(p["fact_ids"]) for p in pages) < 2:
        return _rule_fallback(task, facts, images, strategy, risks, 'invalid_pages')
    if not result["title"].strip() or not result["body"].strip():
        return _rule_fallback(task, facts, images, strategy, risks, 'empty_copy')
    if not result["fact_ids"]:
        result["fact_ids"] = list(dict.fromkeys(fid for p in pages for fid in p["fact_ids"]))
    result["pages"] = pages
    result["tags"] = [t.strip() for t in result["tags"] if t.strip()][:6]
    if not result["cta"].strip():
        result["cta"] = rule["cta"]
    if not result["image_plan"]:
        result["image_plan"] = [{"image_id": image.image_id, "position": "封面" if i == 0 else f"第{i}张",
                                 "reason": "与当前产品型号一致"} for i, image in enumerate(images)]
    return result, meta


def compose_draft(task, facts, images, strategy, risks, engine="rule", model=None):
    if engine == "llm":
        draft, meta = _llm_based(task, facts, images, strategy, risks, model=model)
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
