"""Local official KB export adapter: real zip reads, no ima network."""
import base64
import zipfile
from pathlib import Path

import pytest

from app.contracts import MaterialBundle, Source
from app.evidence import load_catalog, verified_images
from app.local_kb import LocalKBClient, kb_source, local_kb_ready

MODEL = "HUAWEI MateBook Test（测试版）"
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aWQAAAABJRU5ErkJggg=="
)
TEXT = """# HUAWEI MateBook Test（测试版） - 产品事实

> 合成规格；各配置条件不得合并。

## 来源

- 规格页：https://consumer.huawei.com/cn/test/matebook-test-version/specs/

## 操作系统

TEST OS

## 尺寸与重量

约1.33 kg（圆键盘含电池）

## 屏幕

标准450 nits；柔光500 nits，各版本独立。
"""
INDEX = """文件名,产品型号,来源页面,原始图片URL,官网图片说明,宽度,高度,建议用途
hwpc_matebook-test_主视觉.png,HUAWEI MateBook Test（测试版）,https://consumer.huawei.com/cn/test/matebook-test-version/specs/,https://res.vmallres.com/test.png,测试主视觉,800,800,产品介绍配图
"""


def make_zip(path: Path):
    prefix = "华为PC全板块深度知识库_v7/"
    model = prefix + "P1-产品参数库/01-鸿蒙电脑/HUAWEI MateBook Test（测试版）"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(model + "/产品事实.md", TEXT)
        archive.writestr(model + "/图片索引.csv", INDEX)
        archive.writestr(model + "/images/hwpc_matebook-test_主视觉.png", PNG)
        archive.writestr(prefix + "P8-客户场景库/07-学生学习/学生学习场景卡.md",
                         "# 学生学习与成长场景执行指南\n\n> 合成场景卡\n\n## 边界\n\n仅用于测试。\n")


def test_local_kb_reads_products_scenes_and_images(tmp_path, monkeypatch):
    path = tmp_path / "official-kb.zip"
    make_zip(path)
    monkeypatch.setenv("KB_SOURCE", "local")
    monkeypatch.setenv("LOCAL_KB_ZIP", str(path))
    assert local_kb_ready() and kb_source() == "local"

    import asyncio
    client = LocalKBClient(zip_path=path)

    async def run():
        connection = await client.connect()
        assert connection["source_kind"] == "local_official_export"
        assert connection["official_library"] is False
        entries = await client.catalog()
        assert any(entry["title"] == "产品事实.md" for entry in entries)
        assert any(entry["title"] == "图片索引.csv" for entry in entries)
        product_entry = next(entry for entry in entries if entry["title"] == "产品事实.md")
        assert "## 操作系统" in await client.read_text(product_entry)
        image_entry = next(entry for entry in entries if entry["media_type"] == 9)
        assert await client.read_media_bytes(image_entry) == (PNG, "image/png")

        catalog = await load_catalog("大学新生", "课堂学习", client)
        assert len(catalog.products) == 1
        assert catalog.products[0].model == MODEL
        assert catalog.products[0].facts[0].source.kind == "local_official_export"
        assert len(catalog.scene_sources) == 1
        images, issues = await verified_images(catalog, catalog.products[0], "moments", [])
        assert len(images) == 1
        assert images[0]["source"]["kind"] == "local_official_export"
        assert images[0]["verification"]["method"] == "local_official_export_index"
        assert not any("不同图片只有" in issue for issue in issues)

    asyncio.run(run())


def test_live_bundle_accepts_local_official_export(tmp_path):
    source = Source(kind="local_official_export", title="官方知识库", locator="zip", excerpt="测试")
    bundle = MaterialBundle(
        request_id="test-local", data_mode="live", status="ready",
        task={"product_query": MODEL, "audience": "大学新生", "scenario": "课堂学习",
              "objective": "到店体验", "platform": "xiaohongshu"},
        product={"product_id": "P1", "model_name": MODEL, "configuration": "测试", "confirmed": True},
        facts=[{"fact_id": "F1", "product_id": "P1", "text": "屏幕：450 nits", "source": source}],
        images=[{"image_id": "I1", "product_id": "P1", "access_url": "/x", "caption": "测试",
                 "model_verified": True, "source": source}],
    )
    assert bundle.facts[0].source.kind == "local_official_export"
