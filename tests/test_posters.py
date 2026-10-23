import io

from PIL import Image

from app.posters import HEIGHT, WIDTH, render_page


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


def test_single_preview_page_is_1080x1440_png():
    """The single-page renderer survives for the rednote layout/style previews."""
    draft, handoff = _materials()
    content = render_page(draft, handoff, 0)
    assert content.startswith(b"\x89PNG")
    with Image.open(io.BytesIO(content)) as image:
        assert image.size == (WIDTH, HEIGHT)


def test_six_page_plan_and_zip_download_are_gone():
    """规格 §0：3 篇文案取代 7 页图文，六页渲染与长图 ZIP 整套删除。

    原断言是「ZIP 里含 6 张 page-*.png」。新规格下这个交付物不存在，所以结论改为：
    整页渲染/打包入口已经不存在——不是放宽断言，而是断言的方向反过来了。
    """
    import app.posters as posters

    for removed in ("render_all", "pages_for", "zip_bytes"):
        assert not hasattr(posters, removed), removed + " 应当随 7 页图文一起删除"


def test_poster_download_routes_are_gone():
    """长图 ZIP 与单页下载路由必须真的不存在（404），不能只是没人调用。"""
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    assert client.get("/api/v1/conversations/any-session/posters/1.png").status_code == 404
    assert client.get("/api/v1/conversations/any-session/posters.zip").status_code == 404