from fastapi.testclient import TestClient

from app import db as db_layer
from app import design_library
from app.main import app

client = TestClient(app)
PREFIX = "/api/v1/workspaces/default/apps/rednote"


def _use_sqlite(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'design.db'}")
    monkeypatch.setenv("REDNOTE_DATA_DIR", str(tmp_path / "data"))
    db_layer.reset_for_tests(f"sqlite:///{tmp_path / 'design.db'}")


def test_sixteen_families_and_sixty_four_recipes():
    assert len(design_library.FAMILIES) == 16
    assert len(design_library.RECIPES) == 64
    for family_id, *_ in design_library.FAMILIES:
        rows = [recipe for recipe in design_library.RECIPES if recipe.family_id == family_id]
        assert len([row for row in rows if row.role == "cover"]) == 2
        assert len([row for row in rows if row.role == "inner"]) == 2
        # families must differ structurally, not only by colour
        assert len({row.archetype for row in rows}) >= 3


def test_build_document_locks_product_and_binds_text():
    built = design_library.build_document("V08-C1", {"title": "极简桌面的一天"}, "HUAWEI MateBook 14")
    objects = built["native_payload"]["objects"]
    assert any(obj.get("role") == "title" and obj.get("text") == "极简桌面的一天" for obj in objects)
    locked = [obj for obj in objects if obj.get("locked")]
    assert locked and all(obj["type"] == "image-placeholder" for obj in locked)
    assert built["sidecar"]["layout_id"] == "V08-C1"
    assert built["manifest"]["fonts"]
    assert len(built["revision_hash"]) == 64


def test_design_checks_flag_overflow_and_placeholder():
    built = design_library.build_document("V01-C1", {"title": "超" * 40}, "HUAWEI MateBook 14")
    report = design_library.check_document(built)
    assert report["passed"] is False
    codes = {issue["code"] for issue in report["errors"]}
    assert "TEXT_OVERFLOW" in codes
    assert any(issue["code"] == "IMAGE_PLACEHOLDER" for issue in report["warnings"])


def test_design_library_api_and_from_layout(tmp_path, monkeypatch):
    _use_sqlite(tmp_path, monkeypatch)
    styles = client.get(f"{PREFIX}/design-library/styles").json()
    assert len(styles["families"]) == 16
    layouts = client.get(f"{PREFIX}/design-library/layouts?family_id=V15").json()
    assert layouts["count"] == 4

    brief = client.post(f"{PREFIX}/brief-handoffs/consume",
                        json={"product_model": "HUAWEI MateBook 14"}).json()
    created = client.post(f"{PREFIX}/design-documents/from-layout", json={
        "content_item_id": brief["content_item_id"], "layout_id": "V15-I1",
        "bindings": {"title": "这一页看有依据的规格", "body": "只引用简报中的已核实事实。"},
        "product_name": "HUAWEI MateBook 14",
    }).json()
    document_id = created["document"]["id"]
    assert created["checks"]["passed"] is True
    checked = client.post(f"{PREFIX}/design-documents/{document_id}/checks").json()
    assert "warnings" in checked and "errors" in checked


def test_layout_preview_renders_png(tmp_path, monkeypatch):
    _use_sqlite(tmp_path, monkeypatch)
    response = client.get(f"{PREFIX}/design-library/layouts/V16-C1/preview.png")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content.startswith(b"\x89PNG")
    assert client.get(f"{PREFIX}/design-library/layouts/NOPE/preview.png").status_code == 422
