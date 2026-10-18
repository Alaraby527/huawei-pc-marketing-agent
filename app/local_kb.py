"""Read-only adapter for the official Huawei PC knowledge base v7 ZIP export.

The competition's shared ima library is not accessible with every account. This
adapter lets the app use the provided official export without pretending that it
is a live ima call: it reads product facts, scene cards, image indexes and image
bytes directly from the ZIP, and marks the source as ``local_official_export``.
"""
import csv
import hashlib
import io
import os
import re
import threading
import zipfile
from pathlib import Path

from .ima import ImaClient, ImaError, image_type

OFFICIAL_KB_NAME = "华为PC全板块深度知识库_v7（本地官方导出）"
DEFAULT_ZIP = "/app/data/official-kb-v7.zip"
PREFIX = "华为PC全板块深度知识库_v7/"
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
SCENE_TITLES = {
    "学生学习场景卡.md", "移动办公场景卡.md", "内容创作场景卡.md",
    "家庭换新场景卡.md", "企业部署场景卡.md", "商务管理场景卡.md",
}


def local_kb_path():
    raw = os.getenv("LOCAL_KB_ZIP") or DEFAULT_ZIP
    path = Path(raw)
    return path if path.is_file() else None


def local_kb_ready():
    return local_kb_path() is not None


def kb_source():
    explicit = (os.getenv("KB_SOURCE") or "").strip().lower()
    if explicit in {"ima", "local"}:
        return explicit
    return "local" if local_kb_ready() else "ima"


def default_client():
    return LocalKBClient() if kb_source() == "local" else ImaClient()


def _fix_name(name):
    try:
        raw = name.encode("cp437")
    except UnicodeEncodeError:
        return name
    for encoding in ("utf-8", "gbk"):
        try:
            fixed = raw.decode(encoding)
            if any("\u4e00" <= char <= "\u9fff" for char in fixed) or encoding == "utf-8":
                return fixed
        except UnicodeDecodeError:
            continue
    return name


def _heading_model(text):
    heading = re.search(r"^#\s+(.+)", text, re.M)
    if not heading:
        return ""
    title = re.split(r"\s*[-—]\s*产品事实", heading.group(1).strip())[0]
    return title.strip()


class LocalKBClient:
    """Same async surface as ImaClient, backed by a local official ZIP."""

    source_kind = "local_official_export"

    def __init__(self, zip_path=None, kb_name=None):
        self.trace = []
        self.kb_name = kb_name or os.getenv("LOCAL_KB_NAME", OFFICIAL_KB_NAME)
        self.zip_path = Path(zip_path or os.getenv("LOCAL_KB_ZIP") or DEFAULT_ZIP)
        self.kb_id = "local-" + hashlib.sha256(
            (str(self.zip_path) + ":" + str(self.zip_path.stat().st_size if self.zip_path.is_file() else 0)).encode()
        ).hexdigest()[:16]
        self._lock = threading.Lock()
        self._zip = None
        self._names = {}
        self._entries = None
        self._image_csv = None
        self._model_by_dir = {}

    def _open(self):
        if self._zip is not None:
            return
        if not self.zip_path.is_file():
            raise ImaError("本地官方知识库 ZIP 不存在：" + str(self.zip_path))
        try:
            self._zip = zipfile.ZipFile(self.zip_path)
        except (OSError, zipfile.BadZipFile):
            raise ImaError("本地官方知识库 ZIP 无法读取：" + str(self.zip_path)) from None
        self._names = {_fix_name(name): name for name in self._zip.namelist()}

    def _read(self, fixed_name):
        self._open()
        member = self._names.get(fixed_name)
        if member is None:
            raise ImaError("本地知识库文件不存在：" + fixed_name)
        with self._lock:
            return self._zip.read(member)

    def _read_text(self, name):
        try:
            return self._read(name).decode("utf-8-sig")
        except UnicodeDecodeError:
            raise ImaError("本地知识库文件不是 UTF-8：" + name) from None

    async def connect(self):
        self._open()
        self._build_entries()
        self.trace.append({"step": "local_kb_connect", "status": "success"})
        return {
            "knowledge_base_name": self.kb_name,
            "connection": "verified_local_export",
            "official_library": False,
            "source_kind": self.source_kind,
            "note": "官方知识库 v7 本地导出；读取真实文件，但不是实时 ima 调用，赛事指定库要求仍不算满足",
        }

    def _build_entries(self):
        if self._entries is not None:
            return
        self._open()
        entries = []
        for name in self._names:
            if name.startswith(PREFIX + "P1-产品参数库/") and name.endswith("/产品事实.md"):
                text = self._read_text(name)
                model = _heading_model(text)
                parent = name.rsplit("/", 1)[0]
                if model:
                    self._model_by_dir[parent] = model
                entries.append({"media_id": "doc:" + name, "title": "产品事实.md",
                                "media_type": 7, "_path": parent, "_model": model})
            elif name.startswith(PREFIX + "P8-客户场景库/") and name.rsplit("/", 1)[-1] in SCENE_TITLES:
                parent = name.rsplit("/", 1)[0]
                entries.append({"media_id": "doc:" + name, "title": name.rsplit("/", 1)[-1],
                                "media_type": 7, "_path": parent, "_scene_verified": True})
            elif name.startswith(PREFIX + "P1-产品参数库/"):
                suffix = Path(name).suffix.lower()
                if suffix in IMAGE_EXTS and ("/images/" in name or "/商城款式图片/" in name):
                    parent = name.rsplit("/", 1)[0]
                    entries.append({"media_id": "img:" + name, "title": name.rsplit("/", 1)[-1],
                                    "media_type": 9, "_path": parent})
        entries.append({"media_id": "index:combined", "title": "图片索引.csv", "media_type": 5,
                        "_path": PREFIX + "P1-产品参数库/00-全库横向索引/图片索引.csv"})
        self._entries = entries
        self.trace.append({"step": "local_kb_catalog", "status": "success", "entries": len(entries)})

    async def catalog(self):
        self._build_entries()
        return list(self._entries)

    async def search(self, query):
        self._build_entries()
        tokens = [token for token in re.split(r"[^A-Za-z0-9\u4e00-\u9fff]+", (query or "").lower()) if len(token) >= 2]
        if not tokens:
            return []
        matched = []
        for entry in self._entries:
            haystack = (entry.get("_path", "") + " " + entry.get("title", "")).lower()
            if any(token in haystack for token in tokens):
                matched.append(entry)
        self.trace.append({"step": "local_kb_search", "status": "success", "hits": len(matched)})
        return matched

    async def read_text(self, entry):
        media_id = entry.get("media_id", "")
        if media_id == "index:combined":
            return self._build_image_csv()
        if not media_id.startswith("doc:"):
            raise ImaError("本地知识库只支持 Markdown/CSV 原文读取")
        return self._read_text(media_id[4:])

    async def media(self, media_id):
        return {"url_info": {"url": "local://" + media_id}}

    async def read_media_bytes(self, entry):
        media_id = entry.get("media_id", "")
        if not media_id.startswith("img:"):
            raise ImaError("本地知识库素材编号无效")
        data = self._read(media_id[4:])
        mime = image_type(data)
        if not mime:
            raise ImaError("本地知识库图片签名无效")
        return data, mime

    async def libraries(self):
        return [{"name": self.kb_name, "description": "官方知识库 v7 本地只读导出",
                 "role": "local", "type": "zip"}]

    def _build_image_csv(self):
        if self._image_csv is not None:
            return self._image_csv
        rows = []
        seen = set()
        for name in self._names:
            if not (name.startswith(PREFIX + "P1-产品参数库/") and name.endswith("/图片索引.csv")):
                continue
            parent = name.rsplit("/", 1)[0]
            model_dir = parent[:-len("/商城款式图片")] if parent.endswith("/商城款式图片") else parent
            default_model = self._model_by_dir.get(model_dir, "")
            try:
                source = self._read_text(name)
                for row in csv.DictReader(io.StringIO(source)):
                    filename = (row.get("文件名") or "").strip()
                    if not filename or filename in seen:
                        continue
                    seen.add(filename)
                    rows.append({
                        "文件名": filename,
                        "产品型号": (row.get("产品型号") or default_model).strip(),
                        "来源页面": (row.get("来源页面") or "").strip(),
                        "原始图片URL": (row.get("原始图片URL") or "").strip(),
                        "官网图片说明": (row.get("官网图片说明") or "").strip(),
                        "宽度": (row.get("宽度") or "").strip(),
                        "高度": (row.get("高度") or "").strip(),
                        "建议用途": (row.get("建议用途") or "").strip(),
                        "画面说明": (row.get("画面说明") or row.get("官网图片说明") or "").strip(),
                        "商品页URL": (row.get("商品页URL") or row.get("来源页面") or "").strip(),
                        "抓取日期": (row.get("抓取日期") or "").strip(),
                    })
            except (ImaError, csv.Error):
                continue
        output = io.StringIO()
        fieldnames = ["文件名", "产品型号", "来源页面", "原始图片URL", "官网图片说明",
                      "宽度", "高度", "建议用途", "画面说明", "商品页URL", "抓取日期"]
        writer = csv.DictWriter(output, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
        self._image_csv = output.getvalue()
        self.trace.append({"step": "local_kb_image_index", "status": "success", "rows": len(rows)})
        return self._image_csv
