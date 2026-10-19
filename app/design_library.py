"""Design library for the Rednote workspace (M2 foundation).

16 visual families x (2 cover + 2 inner) = 64 layout recipes, each producing a
Fabric-compatible DesignDocument payload plus a semantic sidecar. The recipes
are original JSON structures authored here; no third-party template assets or
fonts are bundled (Apache-2.0 repo, commercially usable resources only).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

CANVAS = {"width": 1080, "height": 1440}
FONTS = ["Noto Sans SC", "Noto Serif SC"]

# Structural archetypes: element blueprints differ per recipe so families are
# not "same layout, different colour".
ARCHETYPES = {
    "poster_center": [
        ("title", 80, 150, 920, 260, 72),
        ("image", 90, 470, 900, 520),
        ("body", 90, 1040, 900, 220, 30),
        ("chips", 90, 1290, 900, 70, 20),
    ],
    "poster_split": [
        ("title", 80, 140, 520, 300, 64),
        ("image", 620, 140, 380, 520),
        ("body", 80, 520, 520, 260, 28),
        ("chips", 80, 840, 920, 70, 20),
    ],
    "magazine_columns": [
        ("kicker", 80, 120, 500, 60, 26),
        ("title", 80, 190, 920, 240, 66),
        ("image", 80, 470, 560, 420),
        ("body", 680, 470, 320, 420, 24),
        ("chips", 80, 950, 920, 70, 20),
    ],
    "timeline": [
        ("title", 80, 140, 920, 200, 60),
        ("step1", 120, 420, 840, 200, 28),
        ("step2", 120, 660, 840, 200, 28),
        ("step3", 120, 900, 840, 200, 28),
        ("chips", 120, 1180, 840, 70, 20),
    ],
    "checklist": [
        ("title", 80, 150, 920, 220, 62),
        ("item1", 110, 430, 860, 150, 28),
        ("item2", 110, 620, 860, 150, 28),
        ("item3", 110, 810, 860, 150, 28),
        ("item4", 110, 1000, 860, 150, 28),
    ],
    "comic_frames": [
        ("frame1", 80, 150, 440, 420, 40),
        ("frame2", 560, 150, 440, 420, 40),
        ("frame3", 80, 620, 440, 420, 40),
        ("frame4", 560, 620, 440, 420, 40),
        ("caption", 80, 1100, 920, 160, 28),
    ],
    "notification_card": [
        ("bubble1", 100, 200, 780, 220, 30),
        ("bubble2", 200, 470, 780, 220, 30),
        ("bubble3", 100, 740, 780, 220, 30),
        ("reply", 200, 1010, 780, 200, 30),
    ],
    "data_evidence": [
        ("title", 80, 140, 920, 200, 60),
        ("chart", 90, 400, 900, 520),
        ("source", 90, 960, 900, 90, 22),
        ("limits", 90, 1090, 900, 200, 26),
    ],
    "film_strip": [
        ("frame1", 60, 200, 960, 380, 44),
        ("subtitle", 90, 620, 900, 120, 30),
        ("frame2", 60, 780, 960, 380, 44),
        ("credits", 90, 1200, 900, 120, 22),
    ],
    "hand_drawn": [
        ("title", 90, 150, 900, 200, 58),
        ("sketch", 140, 420, 800, 460),
        ("arrow_note", 90, 920, 900, 180, 26),
        ("steps", 90, 1140, 900, 180, 24),
    ],
    "isometric_stage": [
        ("title", 80, 140, 920, 200, 60),
        ("stage", 120, 400, 840, 560),
        ("legend", 90, 1010, 900, 160, 24),
        ("chips", 90, 1210, 900, 70, 20),
    ],
    "game_quest": [
        ("quest_title", 90, 150, 900, 200, 60),
        ("progress", 110, 400, 860, 140, 28),
        ("reward", 110, 590, 860, 300, 30),
        ("checklist", 110, 940, 860, 260, 26),
    ],
    "collage": [
        ("tape_title", 120, 160, 840, 220, 56),
        ("photo_a", 90, 440, 420, 400, 32),
        ("photo_b", 570, 440, 420, 400, 32),
        ("note", 120, 900, 840, 260, 28),
    ],
    "minimal_product": [
        ("title", 100, 200, 880, 220, 60),
        ("product", 240, 480, 600, 620, 40),
        ("caption", 140, 1140, 800, 140, 26),
    ],
    "retro_press": [
        ("masthead", 80, 110, 920, 110, 40),
        ("headline", 80, 240, 920, 260, 64),
        ("image", 80, 540, 920, 400),
        ("columns", 80, 980, 920, 260, 24),
    ],
    "future_console": [
        ("title", 80, 140, 920, 200, 58),
        ("panel", 90, 400, 900, 500),
        ("metrics", 90, 940, 900, 160, 24),
        ("note", 90, 1140, 900, 160, 24),
    ],
}

FAMILIES = [
    ("V01", "生活摄影", "真实环境、自然构图、轻批注", "通勤 / 桌面 / 旅行"),
    ("V02", "情绪氛围", "光影、色温、留白、意象", "共鸣与感受"),
    ("V03", "电影分镜", "镜头顺序、字幕、叙事", "微故事"),
    ("V04", "杂志编辑", "网格、大标题、侧栏、刊物节奏", "品牌与生活方式"),
    ("V05", "拼贴手账", "纸片、贴纸、便签、手写", "生活记录"),
    ("V06", "原创梗图", "简洁反差、节奏和包袱", "幽默热点"),
    ("V07", "漫画拟人", "原创角色、气泡、格子", "产品与情绪故事"),
    ("V08", "极简产品", "准确实物、大留白、少量文字", "设计亮点"),
    ("V09", "潮流海报", "大字、冲突色、形状、强动势", "活动与年轻人群"),
    ("V10", "未来科技", "原创信息界面、光效、结构线", "功能解释"),
    ("V11", "游戏任务", "任务卡、进度、成就、道具", "场景参与"),
    ("V12", "复古胶片", "胶片、报刊、旧纸、打字", "故事与情绪"),
    ("V13", "手绘说明书", "线稿、箭头、手写、步骤", "教程与解释"),
    ("V14", "纸艺等距", "原创立体纸景、等距空间", "场景化品牌图"),
    ("V15", "数据证据", "标注、图表、来源、限制", "真实实验或决策"),
    ("V16", "对话通知", "原创聊天/通知卡、气泡", "人群共鸣、答疑"),
]

FAMILY_ARCHETYPES = {
    "V01": ["poster_split", "collage", "timeline", "checklist"],
    "V02": ["poster_center", "minimal_product", "magazine_columns", "timeline"],
    "V03": ["film_strip", "comic_frames", "timeline", "notification_card"],
    "V04": ["magazine_columns", "poster_split", "retro_press", "checklist"],
    "V05": ["collage", "notification_card", "checklist", "timeline"],
    "V06": ["poster_split", "comic_frames", "notification_card", "checklist"],
    "V07": ["comic_frames", "notification_card", "timeline", "poster_center"],
    "V08": ["minimal_product", "poster_center", "poster_split", "checklist"],
    "V09": ["poster_center", "poster_split", "film_strip", "magazine_columns"],
    "V10": ["future_console", "data_evidence", "timeline", "magazine_columns"],
    "V11": ["game_quest", "checklist", "timeline", "notification_card"],
    "V12": ["retro_press", "film_strip", "collage", "magazine_columns"],
    "V13": ["hand_drawn", "timeline", "checklist", "comic_frames"],
    "V14": ["isometric_stage", "poster_center", "timeline", "collage"],
    "V15": ["data_evidence", "magazine_columns", "checklist", "poster_split"],
    "V16": ["notification_card", "comic_frames", "checklist", "poster_center"],
}

PALETTES = {
    "V01": ("#F7F5F0", "#20211F", "#0A59F7"),
    "V02": ("#F3F1F7", "#241F33", "#7C5CFF"),
    "V03": ("#101215", "#F5F5F5", "#E8590C"),
    "V04": ("#FBFAF7", "#1B1B1B", "#111111"),
    "V05": ("#FBF3E4", "#3A2E1F", "#E8590C"),
    "V06": ("#FFFFFF", "#111111", "#FF4D4F"),
    "V07": ("#FFF8E7", "#2B2118", "#0A59F7"),
    "V08": ("#FAFAFA", "#161616", "#0A59F7"),
    "V09": ("#0A0A0A", "#FFFFFF", "#FF2D55"),
    "V10": ("#08131F", "#E6F1FF", "#22D3EE"),
    "V11": ("#141A2E", "#F2F5FF", "#FACC15"),
    "V12": ("#F1E6D0", "#3B2E22", "#B45309"),
    "V13": ("#FFFDF7", "#2B2B2B", "#0A59F7"),
    "V14": ("#F4F7FB", "#22303F", "#0E9F6E"),
    "V15": ("#F7F8FA", "#111827", "#6D4AFF"),
    "V16": ("#F2F4F7", "#1F2937", "#0E9F6E"),
}


@dataclass
class Recipe:
    id: str
    family_id: str
    family_name: str
    role: str
    name: str
    archetype: str
    canvas: dict = field(default_factory=lambda: dict(CANVAS))
    elements: list = field(default_factory=list)
    tokens: dict = field(default_factory=dict)
    constraints: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"id": self.id, "family_id": self.family_id, "family_name": self.family_name,
                "role": self.role, "name": self.name, "archetype": self.archetype,
                "canvas": self.canvas, "elements": self.elements, "tokens": self.tokens,
                "constraints": self.constraints}


def _elements(archetype: str, accent: str, ink: str) -> list:
    elements = []
    for index, spec in enumerate(ARCHETYPES[archetype]):
        role, x, y, w, h = spec[:5]
        size = spec[5] if len(spec) > 5 else 28
        element = {"role": role, "left": x, "top": y, "width": w, "height": h,
                   "fontSize": size, "z": index + 2}
        if role.startswith(("image", "photo", "frame", "stage", "product", "sketch", "panel", "chart")):
            element["type"] = "image-placeholder"
            element["fill"] = "#FFFFFF"
            element["locked"] = role in ("product", "image")
            element["note"] = "产品/素材图层锁定，替换时保留外观"
        else:
            element["type"] = "textbox"
            element["fill"] = ink if role in ("title", "headline", "masthead", "quest_title") else ink
            element["text"] = ""
            element["semanticRole"] = role
        elements.append(element)
    elements.insert(0, {"role": "background", "type": "rect", "left": 0, "top": 0,
                        "width": CANVAS["width"], "height": CANVAS["height"], "fill": accent, "z": 1})
    elements.append({"role": "brand", "type": "textbox", "left": 72, "top": 60, "width": 240,
                     "height": 56, "fontSize": 22, "text": "HUAWEI", "fill": ink, "z": 99})
    return elements


def build_recipes() -> list[Recipe]:
    recipes: list[Recipe] = []
    for family_id, family_name, _visual, _scene in FAMILIES:
        paper, ink, accent = PALETTES[family_id]
        archetypes = FAMILY_ARCHETYPES[family_id]
        roles = ["cover", "cover", "inner", "inner"]
        suffixes = ["C1", "C2", "I1", "I2"]
        for archetype, role, suffix in zip(archetypes, roles, suffixes):
            recipe = Recipe(
                id=f"{family_id}-{suffix}", family_id=family_id, family_name=family_name,
                role=role, name=f"{family_name}·{'封面' if role == 'cover' else '内页'}{suffix[1]}",
                archetype=archetype, elements=_elements(archetype, paper, ink),
                tokens={"paper": paper, "ink": ink, "accent": accent, "fonts": FONTS},
                constraints={"titleMax": 24, "bodyMax": 180, "imageRequired": role in ("cover", "inner"),
                             "productLayerLocked": True},
            )
            recipes.append(recipe)
    return recipes


RECIPES = build_recipes()
RECIPE_INDEX = {recipe.id: recipe for recipe in RECIPES}


def styles() -> list[dict]:
    return [{"family_id": family_id, "name": name, "visual": visual, "scene": scene,
             "covers": [recipe.id for recipe in RECIPES if recipe.family_id == family_id and recipe.role == "cover"],
             "inners": [recipe.id for recipe in RECIPES if recipe.family_id == family_id and recipe.role == "inner"]}
            for family_id, name, visual, scene in FAMILIES]


def layouts(family_id: str | None = None) -> list[dict]:
    return [recipe.as_dict() for recipe in RECIPES if not family_id or recipe.family_id == family_id]


def get_recipe(layout_id: str) -> Recipe | None:
    return RECIPE_INDEX.get(layout_id)


def build_document(layout_id: str, bindings: dict | None = None, product_name: str = "") -> dict:
    recipe = get_recipe(layout_id)
    if not recipe:
        raise KeyError(layout_id)
    bindings = bindings or {}
    native = {"version": "5.3.0", "objects": json.loads(json.dumps(recipe.elements)),
              "background": recipe.tokens["paper"]}
    for element in native["objects"]:
        role = element.get("role")
        if role in bindings:
            element["text"] = str(bindings[role])[: recipe.constraints["bodyMax"]]
    sidecar = {"layout_id": layout_id, "family_id": recipe.family_id, "role": recipe.role,
               "bindings": {key: value for key, value in bindings.items() if isinstance(value, str)},
               "product": product_name,
               "facts": bindings.get("fact_ids", []) if isinstance(bindings.get("fact_ids"), list) else []}
    manifest = {"assets": [], "fonts": recipe.tokens["fonts"],
                "canvas": recipe.canvas, "lockedLayers": ["product", "image"],
                "source": "self-authored-layout-recipe"}
    payload = {"native_payload": native, "sidecar": sidecar, "manifest": manifest,
               "engine": "vue-fabric-editor", "engine_version": "5.3.0"}
    payload["revision_hash"] = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return payload


def check_document(document: dict) -> dict:
    """Deterministic checks (D10 partial): canvas, fonts, overflow estimate, empty placeholders."""
    manifest = document.get("manifest") or {}
    native = document.get("native_payload") or {}
    issues: list[dict] = []
    canvas = manifest.get("canvas") or {}
    if canvas.get("width") != CANVAS["width"] or canvas.get("height") != CANVAS["height"]:
        issues.append({"level": "error", "code": "CANVAS_SIZE",
                       "message": "画布不是 1080×1440，导出规格不一致"})
    for font in manifest.get("fonts", []):
        if font not in FONTS:
            issues.append({"level": "warning", "code": "FONT_NOT_REGISTERED",
                           "message": f"字体 {font} 未登记，可能渲染不一致"})
    for element in native.get("objects", []):
        if element.get("type") == "textbox" and element.get("text"):
            limit = 24 if element.get("semanticRole") in ("title", "headline", "quest_title") else 180
            if len(element["text"]) > limit:
                issues.append({"level": "error", "code": "TEXT_OVERFLOW",
                               "message": f"{element.get('role')} 文字超过 {limit} 字",
                               "element": element.get("role")})
        if element.get("type") == "image-placeholder" and not element.get("asset"):
            issues.append({"level": "warning", "code": "IMAGE_PLACEHOLDER",
                           "message": f"{element.get('role')} 还是占位图，发布前需替换为授权素材",
                           "element": element.get("role")})
    return {"passed": not any(issue["level"] == "error" for issue in issues),
            "errors": [issue for issue in issues if issue["level"] == "error"],
            "warnings": [issue for issue in issues if issue["level"] == "warning"],
            "checked": len(native.get("objects", []))}
