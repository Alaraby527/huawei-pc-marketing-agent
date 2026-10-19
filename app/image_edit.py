"""One-click image generation: prompt + product photo -> edited image.

Uses DashScope's 通义万相图像编辑 (wanx2.1-imageedit, async task API). The
reference product photo is sent as a base64 data URL, so no public hosting or
third-party image storage is involved. Product appearance is preserved by the
prompt; generated images are atmosphere/background only and never claim to be
product photography.
"""
from __future__ import annotations

import base64
import io
import json
import re
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

NATIVE_BASE = (os.getenv("LLM_IMAGE_NATIVE_BASE") or "https://dashscope.aliyuncs.com/api/v1").rstrip("/")
DEFAULT_EDIT_MODEL = os.getenv("LLM_IMAGE_EDIT_MODEL", "wanx2.1-imageedit")
STYLES = {
    "clean_desk": "清爽浅色桌面，柔和自然光，留白充足",
    "campus": "校园教室或图书馆场景，明亮青春",
    "commute": "通勤咖啡馆或地铁场景，都市质感",
    "home": "居家客厅暖光场景，生活气息",
    "tech": "深色科技感背景，冷色光线与结构线",
    "festival": "节日氛围背景，暖色点缀，不出现品牌标识",
}


class ImageEditError(Exception):
    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.message = message
        self.status = status


def configured() -> bool:
    return bool(os.getenv("LLM_API_KEY"))


def _api_key() -> str:
    key = os.getenv("LLM_API_KEY") or ""
    if not key:
        raise ImageEditError("图像编辑通道未配置 LLM_API_KEY", 503)
    return key


def _post(path: str, payload: dict, extra_headers: dict | None = None, timeout: int = 60) -> dict:
    headers = {"Authorization": "Bearer " + _api_key(), "Content-Type": "application/json"}
    if extra_headers:
        headers.update(extra_headers)
    request = urllib.request.Request(NATIVE_BASE + path, data=json.dumps(payload).encode(),
                                     headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        detail = error.read().decode(errors="replace")[:300]
        raise ImageEditError(f"图像编辑接口返回 {error.code}：{detail}", 502) from None
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise ImageEditError("图像编辑接口连接失败：" + str(error)[:200], 502) from None


def _clean(value: str, limit: int = 60) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def build_prompt(scene: str = "", audience: str = "", objective: str = "", title: str = "",
                 facts: list[str] | None = None, style: str = "clean_desk",
                 extra: str = "") -> str:
    style_text = STYLES.get(style, STYLES["clean_desk"])
    scene, audience, objective, title, extra = (_clean(scene, 40), _clean(audience, 20),
                                                _clean(objective, 20), _clean(title, 30), _clean(extra, 80))
    parts = ["小红书封面氛围图"]
    if scene:
        parts.append(f"使用场景：{scene}")
    if audience:
        parts.append(f"目标人群：{audience}")
    if title:
        parts.append(f"主题：{title}")
    if facts:
        parts.append("可体现的已确认卖点：" + "、".join(facts[:3]))
    parts.append(f"背景风格：{style_text}")
    if objective:
        parts.append(f"内容目标：{objective}")
    if extra:
        parts.append(f"补充要求：{extra}")
    parts.append(f"把画面背景整体替换为上述场景，让产品自然融入其中；产品以参考图为准，"
                 f"保持型号、外观与 Logo 正确，不虚构不存在的部件或参数；"
                 f"画面自然好看、构图留出文字空间；如出现文字必须是正确简短的中文，不要乱码、水印、价格或促销信息；"
                 f"生成图仅作氛围背景，不冒充产品实拍。")
    return "。".join(parts)


def _flatten(image_bytes: bytes, max_side: int = 2048) -> tuple[bytes, str]:
    """Composite transparent product cutouts onto a light canvas so the edit
    model has a real background to replace (transparent PNGs otherwise turn
    into black output)."""
    try:
        from PIL import Image
        image = Image.open(io.BytesIO(image_bytes))
        image.load()
        if max(image.size) > max_side:
            image.thumbnail((max_side, max_side), Image.LANCZOS)
        if image.mode in ("RGBA", "LA") or "transparency" in image.info:
            rgba = image.convert("RGBA")
            canvas = Image.new("RGB", rgba.size, (245, 245, 245))
            canvas.paste(rgba, mask=rgba.getchannel("A"))
            image = canvas
        else:
            image = image.convert("RGB")
        out = io.BytesIO()
        image.save(out, format="PNG")
        return out.getvalue(), "image/png"
    except Exception:
        return image_bytes, "image/png"


def _data_url(image_bytes: bytes, mime: str = "image/png") -> str:
    return f"data:{mime};base64," + base64.b64encode(image_bytes).decode()


def edit_image(image_bytes: bytes, prompt: str, mime: str = "image/png",
               size: str | None = None, n: int = 1, model: str | None = None,
               timeout: int = 240, poll_interval: float = 4.0) -> list[bytes]:
    """Submit an image-edit task and return the generated PNG bytes."""
    if not image_bytes:
        raise ImageEditError("缺少产品原图，无法图生图", 422)
    flattened, mime = _flatten(image_bytes, max_side=2048)
    payload = {
        "model": model or DEFAULT_EDIT_MODEL,
        "input": {"function": "description_edit", "prompt": prompt[:1500],
                  "base_image_url": _data_url(flattened, mime)},
        "parameters": {"n": max(1, min(int(n), 4))},
    }
    if size:
        payload["parameters"]["size"] = size
    submitted = _post("/services/aigc/image2image/image-synthesis", payload,
                      {"X-DashScope-Async": "enable"}, timeout=90)
    task_id = (submitted.get("output") or {}).get("task_id")
    if not task_id:
        raise ImageEditError("图像编辑任务提交失败：" + json.dumps(submitted, ensure_ascii=False)[:200])
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(poll_interval)
        request = urllib.request.Request(NATIVE_BASE + "/tasks/" + task_id,
                                         headers={"Authorization": "Bearer " + _api_key()})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                output = (json.loads(response.read()).get("output") or {})
        except (urllib.error.URLError, TimeoutError, OSError):
            continue
        status = output.get("task_status")
        if status == "SUCCEEDED":
            images = []
            for item in output.get("results") or []:
                url = item.get("url")
                if not url:
                    continue
                try:
                    with urllib.request.urlopen(url, timeout=60) as response:
                        images.append(response.read())
                except (urllib.error.URLError, TimeoutError, OSError):
                    continue
            if not images:
                raise ImageEditError("图像编辑成功但结果下载失败", 502)
            return images
        if status in ("FAILED", "CANCELED", "UNKNOWN"):
            raise ImageEditError("图像编辑失败：" + str(output.get("message") or status)[:200], 502)
    raise ImageEditError("图像编辑超时，请稍后重试", 504)


@dataclass
class GeneratedImage:
    content: bytes
    mime: str = "image/png"


class GenerateRequest(__import__("pydantic").BaseModel):
    model_config = __import__("pydantic").ConfigDict(extra="forbid")
    session_id: str | None = None
    image_id: str | None = None
    prompt: str | None = None
    style: str = "clean_desk"
    extra: str = ""
    n: int = 1
    size: str | None = None
    model: str | None = None
    mode: str = "auto"  # auto | scene | edit
    source: str = "ai"  # ai | kb
    ratio: str = "kb"  # kb | 3:4 | 1:1 | 16:9 | 9:16 | 4:3


def has_alpha(image_bytes: bytes) -> bool:
    try:
        from PIL import Image
        image = Image.open(io.BytesIO(image_bytes))
        return image.mode in ("RGBA", "LA") or "transparency" in image.info
    except Exception:
        return False


def _composite(product_bytes: bytes, background_bytes: bytes,
               canvas=(1080, 1440), product_scale: float = 0.72) -> bytes:
    """Place the untouched product photo on a generated background."""
    from PIL import Image, ImageFilter, ImageOps
    width, height = canvas
    background = Image.open(io.BytesIO(background_bytes)).convert("RGB")
    background = ImageOps.fit(background, (width, height), Image.LANCZOS)
    product = Image.open(io.BytesIO(product_bytes))
    product.load()
    product = product.convert("RGBA")
    max_width = int(width * product_scale)
    max_height = int(height * 0.62)
    product.thumbnail((max_width, max_height), Image.LANCZOS)
    # soft shadow under the product
    shadow = Image.new("RGBA", (product.width + 80, product.height + 80), (0, 0, 0, 0))
    alpha = product.getchannel("A").point(lambda value: int(value * 0.35))
    shadow.paste((0, 0, 0, 255), (40, 56), alpha)
    shadow = shadow.filter(ImageFilter.GaussianBlur(18))
    x = (width - product.width) // 2
    y = int(height * 0.30) + (max_height - product.height) // 2
    background = background.convert("RGBA")
    background.alpha_composite(shadow, (x - 40, y - 40))
    background.alpha_composite(product, (x, y))
    out = io.BytesIO()
    background.convert("RGB").save(out, format="PNG")
    return out.getvalue()


def generate_scene_image(product_bytes: bytes, style: str = "clean_desk",
                         prompt: str = "", timeout: int = 300) -> list[bytes]:
    """Background via text-to-image, then composite the original product photo.

    This keeps the product pixels untouched (best for transparent cutouts) while
    still following the requested scene prompt.
    """
    from .model import ModelError, generate_cover_image
    style_text = STYLES.get(style, STYLES["clean_desk"])
    background_prompt = (prompt.strip() + "。" if prompt and prompt.strip() else "") + \
        f"摄影级场景背景：{style_text}；画面中只有桌面、环境与道具，光线自然，构图留出放置笔记本电脑的空间"
    try:
        background, _mime = generate_cover_image(background_prompt[:900])
    except ModelError as error:
        raise ImageEditError("背景生成失败：" + str(error), 502) from None
    return [_composite(product_bytes, background)]
