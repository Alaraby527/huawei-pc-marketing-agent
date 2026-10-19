"""One-click image generation: prompt + product photo -> edited image.

Uses DashScope's 通义万相图像编辑 (wanx2.1-imageedit, async task API). The
reference product photo is sent as a base64 data URL, so no public hosting or
third-party image storage is involved. Product appearance is preserved by the
prompt; generated images are atmosphere/background only and never claim to be
product photography.
"""
from __future__ import annotations

import base64
import json
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


def build_prompt(scene: str = "", audience: str = "", objective: str = "", title: str = "",
                 facts: list[str] | None = None, style: str = "clean_desk",
                 extra: str = "") -> str:
    style_text = STYLES.get(style, STYLES["clean_desk"])
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
    parts.append("严格保留产品原有外观、颜色、Logo、接口与屏幕比例不变，不新增或修改产品部件；"
                 "画面高级自然、构图留出文字空间；不要生成文字、水印、价格、促销或人物面部特写；"
                 "生成图仅作氛围背景，不冒充产品实拍。")
    return "。".join(parts)


def _data_url(image_bytes: bytes, mime: str = "image/png") -> str:
    return f"data:{mime};base64," + base64.b64encode(image_bytes).decode()


def edit_image(image_bytes: bytes, prompt: str, mime: str = "image/png",
               size: str | None = None, n: int = 1, model: str | None = None,
               timeout: int = 240, poll_interval: float = 4.0) -> list[bytes]:
    """Submit an image-edit task and return the generated PNG bytes."""
    if not image_bytes:
        raise ImageEditError("缺少产品原图，无法图生图", 422)
    payload = {
        "model": model or DEFAULT_EDIT_MODEL,
        "input": {"function": "description_edit", "prompt": prompt[:1500],
                  "base_image_url": _data_url(image_bytes, mime)},
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
