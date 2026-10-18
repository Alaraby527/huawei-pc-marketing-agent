"""Render the six-page plan as shareable 1080x1440 PNG posters.

The original prototype in the supplied ZIP renders HTML with Playwright. This
module keeps the same deliverable contract (six fixed roles, official product
images, fact callouts) but renders directly with Pillow so the main app does
not need a browser runtime. It never redraws or invents product appearance:
only the verified material image is placed on the page.
"""
from __future__ import annotations

import io
import os
import threading
import zipfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .ima import read_resource
from .retrieval import get_asset

WIDTH, HEIGHT = 1080, 1440
MARGIN = 76
ROLE_NAMES = {"hook": "封面", "scene": "场景", "proof": "事实",
              "detail": "细节", "decision": "决策", "close": "收尾"}
ROLE_COLORS = {
    "hook": ("#0A59F7", "#EAF0FF", "#DCE8FF"),
    "scene": ("#0E9F6E", "#E8F8F1", "#D2F1E4"),
    "proof": ("#6D4AFF", "#F0EDFF", "#E2DBFF"),
    "detail": ("#E8590C", "#FFF3E8", "#FFE2CC"),
    "decision": ("#D6336C", "#FFF0F5", "#FFDCE8"),
    "close": ("#0A59F7", "#EAF0FF", "#DCE8FF"),
}
PAPER = "#F7F8FA"
INK = "#191A1C"
MUTED = "#6B7280"

_FONT_CANDIDATES = {
    False: [
        os.getenv("POSTER_FONT", ""),
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
    ],
    True: [
        os.getenv("POSTER_FONT_BOLD", ""),
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/System/Library/Fonts/PingFang.ttc",
    ],
}
_font_cache: dict[tuple[int, bool], ImageFont.FreeTypeFont | ImageFont.ImageFont] = {}
_cache: dict[tuple, list[bytes]] = {}
_cache_lock = threading.Lock()


def _font(size: int, bold: bool = False):
    key = (size, bold)
    if key in _font_cache:
        return _font_cache[key]
    for candidate in _FONT_CANDIDATES[bold]:
        if candidate and Path(candidate).exists():
            try:
                font = ImageFont.truetype(candidate, size)
                _font_cache[key] = font
                return font
            except OSError:
                continue
    font = ImageFont.load_default()
    _font_cache[key] = font
    return font


def _text_width(draw, text, font):
    return draw.textlength(text, font=font)


def _wrap(draw, text, font, max_width):
    lines = []
    for paragraph in str(text).split("\n"):
        if not paragraph:
            lines.append("")
            continue
        current = ""
        for char in paragraph:
            trial = current + char
            if _text_width(draw, trial, font) <= max_width or not current:
                current = trial
            else:
                lines.append(current.rstrip())
                current = char.lstrip()
        if current:
            lines.append(current.rstrip())
    return lines


def _balance_lines(text, lines, font, draw, max_width):
    """Avoid a one-character orphan on the last line of a display title."""
    if len(lines) < 2 or len(lines[-1]) >= max(4, len(lines[0]) // 3):
        return lines
    joined = "".join(lines)
    middle = len(joined) // 2
    split_at = middle
    for index, char in enumerate(joined):
        if char in "，。；！？,.;!?" and abs(index - middle) <= 6:
            split_at = index + 1
            break
    first, second = joined[:split_at].strip(), joined[split_at:].strip()
    if not first or not second:
        return lines
    if (_text_width(draw, first, font) <= max_width
            and _text_width(draw, second, font) <= max_width):
        return [first, second]
    return lines


def _fit_lines(draw, text, max_width, max_lines, start, minimum, bold=False, balance=False):
    for size in range(start, minimum - 1, -2):
        font = _font(size, bold)
        lines = _wrap(draw, text, font, max_width)
        if len(lines) <= max_lines:
            return font, (_balance_lines(text, lines, font, draw, max_width) if balance else lines)
    font = _font(minimum, bold)
    lines = _wrap(draw, text, font, max_width)[:max_lines]
    if lines:
        lines[-1] = lines[-1][:max(1, len(lines[-1]) - 1)] + "…"
    return font, lines


def _load_image(asset):
    if not asset:
        return None
    url = asset.get("access_url") or ""
    try:
        if "/api/v1/assets/" in url:
            stored = get_asset(url.rsplit("/", 1)[-1])
            if not stored:
                return None
            body = stored[0]
        elif url.startswith(("http://", "https://")):
            body, _ = read_resource(url, None, 8_000_000)
        else:
            return None
        image = Image.open(io.BytesIO(body))
        image.load()
        return image.convert("RGBA")
    except Exception:
        return None


def _cover_image(canvas: Image.Image, image: Image.Image, box):
    x, y, w, h = box
    radius = 26
    shadow = Image.new("RGBA", (w + 24, h + 24), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((12, 12, w + 12, h + 12), radius=radius,
                                             fill=(0, 0, 0, 28))
    canvas.alpha_composite(shadow, (x - 12, y - 12))
    card = Image.new("RGBA", (w, h), (255, 255, 255, 255))
    d = ImageDraw.Draw(card)
    d.rounded_rectangle((0, 0, w - 1, h - 1), radius=radius, fill=(255, 255, 255, 255),
                        outline=(0, 0, 0, 20), width=1)
    contained = ImageOps.contain(image, (w - 56, h - 56), Image.LANCZOS)
    if contained.mode != "RGBA":
        contained = contained.convert("RGBA")
    px = (w - contained.width) // 2
    py = (h - contained.height) // 2
    card.alpha_composite(contained, (px, py))
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), radius=radius, fill=255)
    canvas.paste(card, (x, y), mask)


def _fact_chips(draw, facts, page, x, y, max_width):
    labels = []
    for fact_id in (page.get("fact_ids") or [])[:2]:
        fact = facts.get(fact_id)
        if not fact:
            continue
        label, _, value = str(fact).partition("：")
        if not value:
            label, value = "依据", str(fact)
        label = " ".join(label.split())[:10]
        value = " ".join(value.split())
        labels.append((label, value[:22] + ("…" if len(value) > 22 else "")))
    if not labels:
        return
    cursor = x
    for label, value in labels:
        font = _font(20, True)
        available = max_width - (cursor - x)
        if available < 160:
            break
        text = f"{label}  {value}"
        while _text_width(draw, text, font) + 36 > available and len(value) > 2:
            value = value[:-1]
            text = f"{label}  {value}…"
        width = min(available, int(_text_width(draw, text, font)) + 36)
        draw.rounded_rectangle((cursor, y, cursor + width, y + 52), radius=14,
                               fill=(255, 255, 255, 235), outline=(0, 0, 0, 18), width=1)
        draw.text((cursor + 18, y + 14), text, font=font, fill=INK)
        cursor += width + 12


def render_page(draft, handoff, index: int) -> bytes:
    pages = draft.get("pages") or []
    if not pages:
        raise ValueError("没有六页方案")
    page = pages[index]
    role = page.get("role", "hook")
    accent, tint, second = ROLE_COLORS.get(role, ROLE_COLORS["hook"])
    canvas = Image.new("RGBA", (WIDTH, HEIGHT), PAPER)
    draw = ImageDraw.Draw(canvas)
    draw.ellipse((WIDTH - 360, -220, WIDTH + 220, 360), fill=tint)
    draw.ellipse((-260, HEIGHT - 360, 300, HEIGHT + 220), fill=second)
    draw.rectangle((0, 0, WIDTH, 12), fill=accent)

    # Brand row
    draw.rounded_rectangle((MARGIN, 58, MARGIN + 132, 104), radius=12, fill=accent)
    draw.text((MARGIN + 16, 67), "HUAWEI", font=_font(22, True), fill="#FFFFFF")
    draw.text((WIDTH - MARGIN - 118, 68), f"{index + 1:02d} / 06",
              font=_font(22, True), fill=MUTED)

    draw.text((MARGIN, 150), ROLE_NAMES.get(role, role), font=_font(26, True), fill=accent)
    title_font, title_lines = _fit_lines(draw, page.get("title", ""), WIDTH - MARGIN * 2,
                                         3, 70, 44, True, balance=True)
    y = 196
    for line in title_lines:
        draw.text((MARGIN, y), line, font=title_font, fill=INK)
        y += int(title_font.size * 1.22)

    handoff_images = {image.get("image_id"): image for image in (handoff.get("images") or [])}
    image_asset = handoff_images.get(page.get("image_id"))
    image = _load_image(image_asset)
    image_box = (MARGIN, max(y + 36, 520), WIDTH - MARGIN * 2, 450)
    if image:
        _cover_image(canvas, image, image_box)
    else:
        x, y0, w, h = image_box
        draw.rounded_rectangle((x, y0, x + w, y0 + h), radius=26, fill=(255, 255, 255, 220),
                               outline=(0, 0, 0, 16), width=1)
        draw.text((x + 40, y0 + h // 2 - 22), "官方产品素材待补充",
                  font=_font(28, True), fill=MUTED)

    body_top = image_box[1] + image_box[3] + 52
    body_font, body_lines = _fit_lines(draw, page.get("text", ""), WIDTH - MARGIN * 2,
                                       4, 34, 24)
    y = body_top
    for line in body_lines:
        draw.text((MARGIN, y), line, font=body_font, fill=INK)
        y += int(body_font.size * 1.55)

    facts = {fact.get("fact_id"): fact.get("text", "") for fact in (handoff.get("facts") or [])}
    _fact_chips(draw, facts, page, MARGIN, min(y + 18, 1250), WIDTH - MARGIN * 2)

    draw.line((MARGIN, HEIGHT - 118, WIDTH - MARGIN, HEIGHT - 118), fill=(0, 0, 0, 28), width=1)
    product = (handoff.get("product_identity") or {}).get("model_name", "")
    draw.text((MARGIN, HEIGHT - 92), product, font=_font(20, True), fill=MUTED)
    draw.text((WIDTH - MARGIN - 430, HEIGHT - 92), "配置与功能以对应销售版本为准",
              font=_font(18), fill=MUTED)

    out = io.BytesIO()
    canvas.convert("RGB").save(out, format="PNG", optimize=True)
    return out.getvalue()


def render_all(draft, handoff):
    pages = draft.get("pages") or []
    if not pages:
        raise ValueError("没有六页方案")
    return [render_page(draft, handoff, index) for index in range(min(6, len(pages)))]


def pages_for(session_id: str, revision: int, draft, handoff):
    key = (session_id, revision, draft.get("title", ""), draft.get("body", "")[:48])
    with _cache_lock:
        cached = _cache.get(key)
    if cached:
        return cached
    pages = render_all(draft, handoff)
    with _cache_lock:
        if len(_cache) >= 8:
            _cache.pop(next(iter(_cache)))
        _cache[key] = pages
    return pages


def zip_bytes(pages):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("README.txt",
                         "华为 PC 营销工作台六页图文图片\n"
                         "1080×1440 PNG，共六页；产品外观来自官方知识库素材。\n"
                         "文案为规则/模型辅助生成，发布前请人工复核。\n")
        for index, page in enumerate(pages, 1):
            archive.writestr(f"page-{index:02d}.png", page)
    return output.getvalue()
