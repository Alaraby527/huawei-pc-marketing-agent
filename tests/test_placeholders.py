"""Unresolved placeholders are a delivery gate (package 3: no_unresolved_placeholders)."""
import pytest

from app.runtime.content_policy import (has_unresolved_placeholders,
                                        unresolved_placeholders)

SHOULD_FLAG = [
    "价格待定，到店咨询。",
    "活动时间待确认，请留意门店公告。",
    "{{活动时间}} 到店体验",
    "联系方式：{phone}",
    "限量 XXX 台，先到先得。",
    "价格 ￥X 起",
    "低至 X 折",
    "内存 ____ GB",
    "活动地点：【】",
    "预约电话：（）",
    "标题：TODO",
    "原价 TBD 元",
    "售价 undefined",
    "【价格】详见店内",
    "限时 XX% off",
]

SHOULD_NOT_FLAG = [
    "14.2 英寸 3:2 屏幕，内存 16 GB 可选。",
    "具体配置与活动以门店当天公示为准。",
    "到店可以现场体验键盘手感。",
    "这台机器重量约 1.31 kg。",
    "学生党写作业、看网课都合适。",
    "你可以带常用软件到店试试。",
    "活动规则请咨询门店工作人员。",
    "屏幕分辨率 2880 × 1920。",
]


@pytest.mark.parametrize("text", SHOULD_FLAG)
def test_placeholders_are_flagged(text):
    assert has_unresolved_placeholders(text), text
    assert unresolved_placeholders(text)


@pytest.mark.parametrize("text", SHOULD_NOT_FLAG)
def test_finished_copy_is_not_flagged(text):
    """False positives would block real drafts, so ordinary wording must pass clean."""
    assert not has_unresolved_placeholders(text), unresolved_placeholders(text)


def test_it_reports_the_offending_fragments_in_order():
    found = unresolved_placeholders("价格待定，活动时间待确认，地点【】。")
    assert "待定" in found
    assert "待确认" in found
    assert "【】" in found


def test_empty_text_is_clean():
    assert unresolved_placeholders("") == []
    assert unresolved_placeholders(None) == []


def test_the_notice_says_it_blocks_publication():
    from app.runtime.content_policy import PLACEHOLDER_NOTICE
    assert "不得视为可用稿" in PLACEHOLDER_NOTICE
    assert "价格" in PLACEHOLDER_NOTICE
