"""Deterministic content checks that a prompt alone cannot guarantee.

The platform rules already forbid fabricated personal experience and unverified
product claims (``app/platforms.py``: "不得编造个人体验或软件实测"). Nothing enforced
them: the grounding pass only inspects *numeric* claims bound to a product record, and
only for a saved artifact. Real testing showed why that is not enough — with the
knowledge base unavailable, the model delivered a first-person story ("背了三年游戏本
上班") and an unevidenced spec ("14 英寸") straight into the chat, while correctly
refusing the explicitly forbidden number.

The answer is not another instruction in the prompt. It is a check that flags the
delivery so it cannot be presented as verified.
"""
from __future__ import annotations

import re

# First-person experience that a marketing draft cannot legitimately have lived.
# Deliberately narrow: it targets lived-experience claims, not a brand voice or a
# second-person suggestion ("你可以去店里试试" is fine).
_EXPERIENCE = re.compile(
    r"亲测|本人实测|真实体验|"
    r"上手\s*[一二三四五六七八九十0-9]+\s*(天|周|月|年)|"
    r"(用|背|使|买|换)了\s*[一二三四五六七八九十0-9]+\s*(天|周|月|年)|"
    r"我(用|买|选|换|背|测|试|入手)(了|过)|"
    r"这段时间(用|使|背)下来|"
    r"我的(真实)?(使用|体验|感受)")

EXPERIENCE_NOTICE = ("（提示：以上草稿包含第一人称使用叙述，属于未核实的创作表达，"
                     "不能作为真实体验或实测结论发布。）")
UNVERIFIED_NUMBER_NOTICE = ("（提示：知识库当前不可用，以上数字未经核实，"
                            "不能作为产品参数发布。）")


def fabricated_experience(text: str) -> list[str]:
    """Return the matched first-person experience phrases, if any."""
    return [match.group(0) for match in _EXPERIENCE.finditer(text or "")]


def has_fabricated_experience(text: str) -> bool:
    return bool(fabricated_experience(text))


def unverified_product_numbers(text: str) -> list[str]:
    """Numeric product claims present in prose (page counts etc. are ignored)."""
    from ..grounding import extract_claims
    return [claim.text for claim in extract_claims(text or "")]