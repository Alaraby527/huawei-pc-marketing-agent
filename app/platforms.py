PLATFORM_RULES = {
    # 2026-10-22 项目负责人裁定：4 处字数冲突一律采纳新方案（package3）值，旧值作废
    # （`surfaces.SPEC_DECISION`）。所以这里的 length_guidance 已与 app/surfaces.py 的
    # body_soft_range / chars_soft_range / headline_soft_max 对齐；措辞保持原样，只改数字。
    "xiaohongshu": {"name": "小红书", "image_min": 3, "image_max": 6,
        "tone": "口语化、短句、以问题和场景组织；不编造亲测经历", "length_guidance": "建议350-700字，短段落",
        "title_guidance": "具体人群与真实问题，避免夸张效果承诺", "cta": "留言说明专业、必用软件与携带需求，再核对具体配置。",
        "structure": ["口语化标题", "短段落", "使用场景", "话题标签", "行动引导"],
        "constraints": ["不得编造个人体验或软件实测"]},
    "wechat_article": {"name": "公众号", "image_min": 2, "image_max": 4,
        "tone": "清晰克制、解释充分、分节说明适用条件", "length_guidance": "建议1000-1800字",
        "title_guidance": "任务主题与选择依据", "cta": "查看对应型号规格与条件，携带常用软件或外设到店验证。",
        "structure": ["标题", "导语", "分段正文", "结尾行动引导"], "constraints": []},
    "moments": {"name": "门店朋友圈", "image_min": 1, "image_max": 3,
        "tone": "简洁直接、熟人沟通、只突出一个有证据的卖点", "length_guidance": "建议100-200字",
        "title_guidance": "可用首句代替独立长标题", "cta": "私信常用软件与预算，预约到店体验。",
        "structure": ["短文案", "一个核心卖点", "咨询或到店引导"], "constraints": ["避免卖点堆叠"]},
    "poster": {"name": "门店海报", "image_min": 1, "image_max": 1,
        "tone": "短标题、短卖点、层级明确", "length_guidance": "建议主标题14字内，正文80字内",
        "title_guidance": "明确产品/场景名，副标题补适用人群", "cta": "到店体验，咨询具体配置与适用条件。",
        "structure": ["主标题", "副标题", "不超过三个卖点", "有依据的活动说明"],
        "constraints": ["不拉伸图片", "不错配型号", "不编造活动"]},
}
