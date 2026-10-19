"""Dedicated image channel (better text-to-image / image-to-image models).

Configured separately from the text model:
  IMAGE_API_BASE_URL  default https://maas.qianwenaiapi.com/compatible-mode/v1
  IMAGE_API_KEY       required for this channel
  IMAGE_MODEL         default qwen-image-3.0-pro

The channel is OpenAI-compatible: POST /images/generations. Passing an `image`
data URL switches it to image-to-image (verified: product appearance kept while
the scene changes). DashScope remains as fallback when this channel is absent.
"""
from __future__ import annotations

import base64
import io
import json
import os
import urllib.error
import urllib.request

DEFAULT_BASE = os.getenv("IMAGE_API_BASE_URL", "https://maas.qianwenaiapi.com/compatible-mode/v1").rstrip("/")
DEFAULT_MODEL = os.getenv("IMAGE_MODEL", "qwen-image-3.0-pro")

# Rich, switchable styles. Each entry is a full visual brief; the copywriter
# prompt keeps product facts untouched, these only drive the picture.
STYLES: dict[str, dict] = {
    "digital_handbook": {
        "label": "数码手账",
        "prompt": "数码手账拼贴风：米白纸感背景，胶带与贴纸点缀，手写感线条与涂鸦装饰，产品图带白色描边与阴影，"
                  "暖色小面积强调，画面像手账本的一页，干净清爽",
    },
    "clean_desk": {
        "label": "清爽桌搭",
        "prompt": "清爽桌搭摄影风：浅色木桌或白色桌面，柔和自然光，绿植、水杯、笔记本等少量道具，"
                  "大留白，产品居中偏下，色调干净通透，适合小红书封面",
    },
    "light_comic": {
        "label": "轻漫画",
        "prompt": "轻漫画插画风：扁平色块与柔和渐变，粗描边，圆角对话框与箭头标注，"
                  "马卡龙色系，画面轻松可爱，产品保持真实外观",
    },
    "magazine": {
        "label": "杂志编辑",
        "prompt": "杂志编辑排版风：大标题区、网格分栏、侧栏文字块，纸张质感，克制的黑白灰加一个强调色，"
                  "产品大图作为主视觉",
    },
    "minimal": {
        "label": "极简产品",
        "prompt": "极简产品摄影：纯色或微渐变背景，柔和投影，产品占画面主体，光线干净，"
                  "高级质感，大量留白",
    },
    "tech": {
        "label": "未来科技",
        "prompt": "未来科技风：深色背景，冷色光效与结构线，原创信息面板元素，产品边缘有科技感高光，"
                  "不出现真实品牌软件界面",
    },
    "retro": {
        "label": "复古胶片",
        "prompt": "复古胶片风：暖黄色调、轻微颗粒与暗角，旧纸与报刊质感，怀旧道具，产品自然融入",
    },
    "hand_drawn": {
        "label": "手绘说明",
        "prompt": "手绘说明书风：线稿与手写标注，箭头指向产品细节，浅色纸张背景，"
                  "像产品说明书的一页",
    },
    "campus": {
        "label": "校园学习",
        "prompt": "校园图书馆或教室场景，明亮青春，书架与窗光，桌面上有书本，产品在桌面上自然使用",
    },
    "home": {
        "label": "居家生活",
        "prompt": "居家客厅或书桌暖光场景，生活气息，布艺与绿植，产品在桌面上自然摆放",
    },
    "commute": {
        "label": "通勤办公",
        "prompt": "都市咖啡馆或共享办公场景，通勤质感，咖啡与公文包，产品在桌面上打开使用",
    },
    "festival": {
        "label": "节日氛围",
        "prompt": "节日氛围背景，暖色点缀与灯串，构图喜庆但不出现品牌标识，产品保持原样",
    },
    "photo": {
        "label": "真实场景摄影",
        "prompt": "写实摄影风：真实环境、自然光、浅景深，产品作为主体被自然使用，画面有生活感，"
                  "不出现文字与夸张特效",
    },
}


class ImageChannelError(Exception):
    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.message = message
        self.status = status


def configured() -> bool:
    return bool(os.getenv("IMAGE_API_KEY"))


def style_list() -> list[dict]:
    return [{"id": key, "label": value["label"]} for key, value in STYLES.items()]


def build_prompt(style: str, subject: str = "", scene: str = "", audience: str = "",
                 facts: list[str] | None = None, extra: str = "") -> str:
    spec = STYLES.get(style) or STYLES["clean_desk"]
    parts = [spec["prompt"]]
    if subject:
        parts.append(f"主体：{subject}")
    if scene:
        parts.append(f"使用场景：{scene}")
    if audience:
        parts.append(f"面向人群：{audience}")
    if facts:
        parts.append("可体现的已确认卖点：" + "、".join(facts[:3]))
    if extra:
        parts.append(f"补充要求：{extra}")
    parts.append("让产品自然地融入场景与画风：光影、色调、材质和背景协调统一，像是真实拍摄或同一画风里的一部分；"
                 "同时保持产品型号、外观和 Logo 正确，不要虚构不存在的部件、参数或标识；"
                 "画面自然好看、构图留出文字空间；可以出现少量正确、简短的中文标题（不超过 12 字），"
                 "不要出现乱码、英文、水印、价格或促销信息，也不要出现软件界面、应用图标或第三方品牌 Logo")
    return "。".join(part.strip() for part in parts if part).strip()[:1800]


def _data_url(image_bytes: bytes, mime: str = "image/png") -> str:
    return f"data:{mime};base64," + base64.b64encode(image_bytes).decode()


def _decode(result: dict) -> list[bytes]:
    images: list[bytes] = []
    for item in result.get("data") or []:
        if item.get("b64_json"):
            try:
                images.append(base64.b64decode(item["b64_json"]))
                continue
            except (ValueError, TypeError):
                pass
        url = item.get("url")
        if url:
            try:
                with urllib.request.urlopen(url, timeout=120) as response:
                    images.append(response.read())
            except (urllib.error.URLError, TimeoutError, OSError):
                continue
    if not images:
        raise ImageChannelError("图像通道没有返回可用图片", 502)
    return images


def _request(path: str, data: bytes, content_type: str, timeout: int,
             base: str | None = None, key: str | None = None) -> dict:
    key = key or os.getenv("IMAGE_API_KEY") or ""
    request = urllib.request.Request((base or DEFAULT_BASE) + path, data=data,
                                     headers={"Authorization": "Bearer " + key,
                                              "Content-Type": content_type}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        detail = error.read().decode(errors="replace")[:300]
        raise ImageChannelError(f"图像通道返回 {error.code}：{detail}", 502) from None
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise ImageChannelError("图像通道连接失败：" + str(error)[:200], 502) from None


def _edits(prompt: str, image_bytes: bytes, mime: str, model: str,
           size: str, n: int, timeout: int, base: str | None = None,
           key: str | None = None) -> list[bytes]:
    import uuid
    boundary = "----zcode" + uuid.uuid4().hex
    parts: list[bytes] = []
    fields = [("model", model), ("prompt", prompt[:1800]), ("size", size), ("n", str(max(1, min(n, 4))))]
    for name, value in fields:
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    extension = "png" if "png" in mime else "jpg"
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="image"; '
                 f'filename="product.{extension}"\r\nContent-Type: {mime}\r\n\r\n'.encode())
    parts.append(image_bytes)
    parts.append(f'\r\n--{boundary}--\r\n'.encode())
    return _decode(_request("/images/edits", b"".join(parts),
                            "multipart/form-data; boundary=" + boundary, timeout, base, key))


def _attempt(prompt: str, image_bytes: bytes | None, mime: str, model: str, size: str,
             n: int, timeout: int, base: str, key: str) -> list[bytes]:
    if image_bytes:
        # Prefer the standard OpenAI edits endpoint (gpt-image family). Some
        # gateways reject multipart (404/405) or drop the connection; in both
        # cases fall through to the image field on /images/generations.
        try:
            return _edits(prompt, image_bytes, mime, model, size, n, timeout, base, key)
        except ImageChannelError:
            pass
    payload = {"model": model, "prompt": prompt[:1800], "size": size,
               "n": max(1, min(int(n), 4))}
    if image_bytes:
        payload["image"] = _data_url(image_bytes, mime)
    return _decode(_request("/images/generations", json.dumps(payload).encode(),
                            "application/json", timeout, base, key))


def _fallback_config() -> tuple[str, str, str] | None:
    base = (os.getenv("IMAGE_FALLBACK_BASE_URL") or "").strip()
    key = (os.getenv("IMAGE_FALLBACK_API_KEY") or "").strip()
    if not base or not key:
        return None
    return base, key, (os.getenv("IMAGE_FALLBACK_MODEL") or DEFAULT_MODEL).strip()


def generate(prompt: str, image_bytes: bytes | None = None, mime: str = "image/png",
             model: str | None = None, size: str | None = None, n: int = 1,
             timeout: int = 300, ratio: float | None = None) -> tuple[list[bytes], str]:
    """Return (images, channel_label); retries a fallback channel on 429/5xx.

    When `ratio` is given the output is center-cropped to that aspect ratio.
    """
    if not os.getenv("IMAGE_API_KEY"):
        raise ImageChannelError("图像通道未配置 IMAGE_API_KEY", 503)
    resolved = model or DEFAULT_MODEL
    resolved_size = size or size_for_ratio(ratio or 1.0)
    try:
        images = _attempt(prompt, image_bytes, mime, resolved, resolved_size, n, timeout,
                          DEFAULT_BASE, os.getenv("IMAGE_API_KEY") or "")
        channel = "primary"
    except ImageChannelError as error:
        fallback = _fallback_config()
        retryable = any(code in error.message for code in ("429", "500", "502", "503", "504", "超时", "连接失败"))
        if not fallback or not retryable:
            raise
        base, key, fb_model = fallback
        images = _attempt(prompt, image_bytes, mime, fb_model, resolved_size, n, timeout, base, key)
        channel = "fallback"
    if ratio:
        images = [crop_to_ratio(image, ratio) for image in images]
    return images, channel


def generate_background(prompt: str, model: str | None = None, size: str | None = None,
                        n: int = 1, ratio: float | None = None) -> tuple[list[bytes], str]:
    """Text-to-image background only; the product is composited afterwards."""
    return generate(prompt, None, "image/png", model, size, n, ratio=ratio)


def generate_from_product(product_bytes: bytes, prompt: str, mime: str = "image/png",
                          model: str | None = None, size: str | None = None,
                          n: int = 1, ratio: float | None = None) -> tuple[list[bytes], str]:
    """Image-to-image: product photo + prompt -> (scene images, channel label)."""
    return generate(prompt, product_bytes, mime, model, size, n, ratio=ratio)


SIZE_CANDIDATES = ["1024x1024", "1024x1365", "1024x1536", "1536x1024", "1365x1024",
                   "1440x1080", "1080x1440", "1365x768", "768x1365"]

RATIO_PRESETS = {
    "kb": None,           # follow the reference image
    "3:4": 3 / 4,         # Xiaohongshu default
    "1:1": 1.0,
    "4:3": 4 / 3,
    "16:9": 16 / 9,
    "9:16": 9 / 16,
}


def reference_ratio(image_bytes: bytes) -> float | None:
    try:
        from PIL import Image
        with Image.open(io.BytesIO(image_bytes)) as image:
            return image.width / image.height
    except Exception:
        return None


def size_for_ratio(ratio: float) -> str:
    def value(size: str) -> float:
        width, height = (int(part) for part in size.split("x"))
        return width / height
    return min(SIZE_CANDIDATES, key=lambda size: abs(value(size) - ratio))


def crop_to_ratio(image_bytes: bytes, ratio: float) -> bytes:
    """Center-crop a generated image to the target aspect ratio."""
    try:
        from PIL import Image
        with Image.open(io.BytesIO(image_bytes)) as image:
            image = image.convert("RGB")
            width, height = image.size
            current = width / height
            if abs(current - ratio) < 0.005:
                out = io.BytesIO()
                image.save(out, format="PNG")
                return out.getvalue()
            if current > ratio:  # too wide
                new_width = int(round(height * ratio))
                left = (width - new_width) // 2
                image = image.crop((left, 0, left + new_width, height))
            else:  # too tall
                new_height = int(round(width / ratio))
                top = (height - new_height) // 2
                image = image.crop((0, top, width, top + new_height))
            out = io.BytesIO()
            image.save(out, format="PNG")
            return out.getvalue()
    except Exception:
        return image_bytes
