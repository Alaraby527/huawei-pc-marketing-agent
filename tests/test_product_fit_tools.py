"""The two selection paths need knowledge-base-backed tools.

Path A (the user named a model) needs that model's own audience/scenario conclusion —
the user must not be asked to describe an audience the library already concluded on.
Path B (the usual case) needs cross-model field matrices to match several candidates.

These use a synthetic catalog; the real library is exercised separately.
"""
import asyncio

import pytest

from app import agent_runtime as ar
from app.contracts import Fact, Source


def _fact(fid, text):
    return Fact(fact_id=fid, product_id="P-a", text=text, conditions="",
                source=Source(kind="local_official_export", title="合成材料",
                              locator="合成材料/规格", excerpt="合成"))


class _Product:
    model = "HUAWEI MateBook Test A"
    product_id = "P-a"
    issues = []
    docs = []
    facts = [_fact("F-MEM", "内存：16 GB")]


class _Client:
    async def read_text(self, entry):
        return f"# 卡内容 {entry['title']}"


class _TextClient:
    """A client whose document content is fixed, for exercising shape handling."""

    def __init__(self, text=""):
        self._text = text

    async def read_text(self, entry):
        return self._text


class _Catalog:
    products = [_Product()]
    image_index = []
    client = _Client()
    entries = [
        {"media_id": "doc:fit", "title": "06-适用人群与限制卡.md",
         "_card_model": "HUAWEI MateBook Test A"},
        {"media_id": "doc:id", "title": "01-型号身份卡.md",
         "_card_model": "HUAWEI MateBook Test A"},
        {"media_id": "doc:other", "title": "06-适用人群与限制卡.md",
         "_card_model": "HUAWEI MateBook Other"},
        {"media_id": "doc:w", "title": "尺寸重量矩阵.csv", "_matrix": True},
        {"media_id": "doc:m", "title": "内存矩阵.csv", "_matrix": True},
    ]


def _ctx(catalog=None):
    class _Conv:
        id = "c1"

    class _Run:
        id = "r1"
        events = []

    class _DB:
        def add(self, *args, **kwargs):
            pass

    return ar.ToolContext(db=_DB(), conversation=_Conv(), run=_Run(),
                          catalog=catalog if catalog is not None else _Catalog())


def test_new_tools_are_registered_and_visible_to_the_model():
    assert "read_product_fit" in ar.TOOL_NAMES
    assert "read_field_matrix" in ar.TOOL_NAMES
    # The model only sees tools that are in the native specs the gateway sends.
    native = {item["function"]["name"] for item in ar.NATIVE_TOOL_SPECS}
    assert {"read_product_fit", "read_field_matrix"} <= native


def test_product_fit_returns_the_library_conclusion():
    result = asyncio.run(ar._tool_product_fit(_ctx(), {"model": "MateBook Test A"}))
    assert result["ok"] is True
    assert result["card_model"] == "HUAWEI MateBook Test A"
    assert "适用人群与限制卡" in result["fit"]
    # Facts are not repeated here; they come from read_product_evidence.
    assert result["identity"] == ""
    assert "read_product_evidence" in result["note"]
    # The fit conclusion carries its own conditions; they must be preserved.
    assert "限定条件" in result["note"]


def test_identity_card_is_opt_in():
    result = asyncio.run(ar._tool_product_fit(
        _ctx(), {"product_id": "P-a", "include_identity": True}))
    assert "型号身份卡" in result["identity"]


def test_only_the_fit_sections_are_returned():
    """The card's fact section duplicates read_product_evidence and bloated the prompt."""
    card = ("# HUAWEI MateBook Test A｜06-适用人群与限制卡\n"
            "## 检索结论\n适合移动办公。\n"
            "## 已确认事实\n- 16 GB 内存。\n"
            "## 适用任务\n- 学生与教师。\n"
            "## 不适用或需先验证的任务\n- 未确认的 Windows 专用软件。\n"
            "## 来源入口\n- 产品事实.md\n")

    class One(_Catalog):
        client = _TextClient(card)

    result = asyncio.run(ar._tool_product_fit(_ctx(One()), {"product_id": "P-a"}))
    assert "检索结论" in result["fit"]
    assert "适用任务" in result["fit"]
    assert "不适用或需先验证" in result["fit"]
    assert "已确认事实" not in result["fit"]
    assert "来源入口" not in result["fit"]


def test_truncation_is_marked_not_silent():
    class Huge(_Catalog):
        client = _TextClient("## 适用任务\n" + ("- 学生与教师。\n" * 600))

    result = asyncio.run(ar._tool_product_fit(_ctx(Huge()), {"product_id": "P-a"}))
    assert result["truncated"], "the cut must be reported"
    assert "已截断" in result["fit"]


def test_product_fit_matches_the_right_card_dir_and_not_a_sibling():
    result = asyncio.run(ar._tool_product_fit(_ctx(), {"product_id": "P-a"}))
    assert result["card_model"] == "HUAWEI MateBook Test A"


def test_product_fit_refuses_instead_of_inventing_an_audience():
    class NoCards(_Catalog):
        entries = []

    result = asyncio.run(ar._tool_product_fit(_ctx(NoCards()), {"product_id": "P-a"}))
    assert result["ok"] is False
    assert result["error"] == "FIT_CARD_NOT_FOUND"
    assert "不要自行推断" in result["message"]


def test_unknown_product_is_reported_with_available_options():
    result = asyncio.run(ar._tool_product_fit(_ctx(), {"model": "不存在的型号 2099"}))
    assert result["ok"] is False
    assert result["error"] == "PRODUCT_NOT_FOUND"
    assert result["available"][0]["product_id"] == "P-a"


def test_field_matrix_selects_a_field_and_lists_alternatives():
    result = asyncio.run(ar._tool_field_matrix(_ctx(), {"field": "尺寸重量"}))
    assert result["ok"] is True
    assert result["matrix"] == "尺寸重量矩阵.csv"
    assert "内存矩阵.csv" in result["available"]
    assert "不做综合排名" in result["note"]


def test_unknown_field_lists_what_exists():
    result = asyncio.run(ar._tool_field_matrix(_ctx(), {"field": "不存在的字段"}))
    assert result["ok"] is False
    assert result["error"] == "FIELD_NOT_FOUND"
    assert set(result["available"]) == {"尺寸重量矩阵.csv", "内存矩阵.csv"}


def test_matrix_defaults_to_the_combined_matrix():
    class Combined(_Catalog):
        entries = _Catalog.entries + [
            {"media_id": "doc:all", "title": "全型号参数联合矩阵.csv", "_matrix": True}]

    result = asyncio.run(ar._tool_field_matrix(_ctx(Combined()), {}))
    assert result["ok"] is True
    assert "全型号参数联合矩阵" in result["matrix"]


def test_no_matrices_at_all_is_honest():
    class NoMatrices(_Catalog):
        entries = [entry for entry in _Catalog.entries if not entry.get("_matrix")]

    result = asyncio.run(ar._tool_field_matrix(_ctx(NoMatrices()), {"field": "内存"}))
    assert result["ok"] is False
    assert result["error"] == "MATRIX_NOT_FOUND"