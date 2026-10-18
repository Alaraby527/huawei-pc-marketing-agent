import io
import zipfile

from PIL import Image

from app.posters import HEIGHT, WIDTH, render_all, render_page, zip_bytes


def _materials():
    draft = {
        "title": "大学新生选本",
        "pages": [
            {"role": role, "title": f"{role} 标题", "text": "把真实使用场景放在配置表前面。",
             "fact_ids": ["F-1"], "image_id": ""}
            for role in ("hook", "scene", "proof", "detail", "decision", "close")
        ],
    }
    handoff = {
        "images": [],
        "facts": [{"fact_id": "F-1", "text": "屏幕：14 英寸"}],
        "product_identity": {"model_name": "HUAWEI MateBook 14"},
    }
    return draft, handoff


def test_poster_page_is_1080x1440_png():
    draft, handoff = _materials()
    content = render_page(draft, handoff, 0)
    assert content.startswith(b"\x89PNG")
    with Image.open(io.BytesIO(content)) as image:
        assert image.size == (WIDTH, HEIGHT)


def test_six_pages_zip_contains_all_pages():
    draft, handoff = _materials()
    pages = render_all(draft, handoff)
    assert len(pages) == 6
    with zipfile.ZipFile(io.BytesIO(zip_bytes(pages))) as archive:
        names = archive.namelist()
        assert "README.txt" in names
        assert [name for name in names if name.startswith("page-")] == [
            f"page-{index:02d}.png" for index in range(1, 7)
        ]
