from dataclasses import dataclass

from app.grounding import check_artifact, check_text, extract_claims, records_from_facts


@dataclass
class Source:
    title: str = "合成测试材料"
    locator: str = "合成测试材料/规格"


@dataclass
class Fact:
    fact_id: str
    text: str
    source: Source
    conditions: list | None = None
    source_revision: str = "sha256:demo"


def _records():
    facts = [
        Fact("F-MEM", "内存：16 GB", Source(), [], "sha256:mem"),
        Fact("F-STO", "存储：512 GB", Source(), [], "sha256:sto"),
        Fact("F-WEIGHT", "尺寸与重量：约 1.33 kg", Source(), [], "sha256:weight"),
        Fact("F-SCREEN", "屏幕：14.2 英寸", Source(), [], "sha256:screen"),
        Fact("F-BAT", "电池：70 Wh", Source(), ["仅合成测试配置"], "sha256:bat"),
    ]
    return records_from_facts(facts, product_id="DEMO-A", variant_id="DEMO-A-BASE")


def test_records_keep_value_unit_and_qualifier():
    records = {record.fact_id: record for record in _records()}
    assert records["F-MEM"].value == "16" and records["F-MEM"].unit.lower() == "gb"
    assert records["F-WEIGHT"].qualifier == "约"
    assert records["F-BAT"].conditions == ["仅合成测试配置"]


def test_correct_claims_pass():
    report = check_text("16 GB 内存，512 GB 存储，14.2 英寸屏幕", _records())
    assert report["passed"] is True
    assert report["checked"] == 3
    assert not report["errors"]


def test_cross_variant_or_unknown_value_is_rejected():
    report = check_text("32 GB 内存，1 TB 存储", _records())
    assert report["passed"] is False
    assert {item["code"] for item in report["errors"]} == {"FACT_NOT_FOUND"}


def test_exact_unit_conversion_is_allowed():
    report = check_text("0.5 TB 存储", _records())
    assert report["passed"] is True


def test_non_product_numbers_are_ignored():
    report = check_text("给你 3 个标题，7 天后再发，收藏率 5%", _records())
    assert report["checked"] == 0
    assert report["passed"] is True


def test_dropped_approximation_is_flagged():
    report = check_text("重量 1.33 kg", _records())
    assert report["passed"] is True
    assert report["warnings"][0]["code"] == "QUALIFIER_DROPPED"


def test_wrong_weight_is_rejected():
    report = check_text("重量 1.5 kg", _records())
    assert report["passed"] is False
    assert report["errors"][0]["code"] == "FACT_NOT_FOUND"


def test_price_claims_are_rejected_without_price_evidence():
    report = check_text("现在只要 5899 元", _records())
    assert report["passed"] is False
    assert report["errors"][0]["code"] == "PRICE_CLAIM"


def test_conditions_must_be_visible():
    report = check_text("70 Wh 电池", _records())
    assert report["passed"] is True
    assert any(item["code"] == "CONDITION_MISSING" for item in report["warnings"])


def test_artifact_checks_all_text_surfaces_and_reports_paths():
    content = {
        "title": "16 GB 内存够用吗",
        "body": "这台机器是 32 GB 内存。",
        "pages": [
            {"title": "512 GB 存储", "text": "14.2 英寸屏幕"},
            {"title": "重量 1.5 kg"},
        ],
        "tags": ["大学生", "华为"],
    }
    report = check_artifact(content, _records())
    assert report["passed"] is False
    paths = {item.get("path") for item in report["errors"]}
    assert "body" in paths and "pages[1].title" in paths


def test_claim_extraction_handles_case_and_spacing():
    claims = extract_claims("16GB / 512 gb / 70WH")
    assert len(claims) == 3
