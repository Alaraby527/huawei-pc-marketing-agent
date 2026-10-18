"""Versioned handoff contract shared by retrieval and composition owners."""
from enum import Enum
from typing import Literal
from pydantic import BaseModel, Field, ConfigDict, model_validator

class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

class Platform(str, Enum):
    XIAOHONGSHU = "xiaohongshu"
    WECHAT_ARTICLE = "wechat_article"
    MOMENTS = "moments"
    POSTER = "poster"

PAGE_ROLES = ("hook", "scene", "proof", "detail", "decision", "close")

class Task(StrictModel):
    product_query: str = Field(min_length=1, max_length=300)
    audience: str = Field(min_length=1, max_length=300)
    scenario: str = Field(min_length=1, max_length=300)
    objective: str = Field(min_length=1, max_length=300)
    platform: Platform
    purchase_stage: str = Field(default="待确认", max_length=100)
    instructions: str = Field(default="", max_length=2000)
    hotspot: str = Field(default="", max_length=300)

class Source(StrictModel):
    kind: Literal["ima", "local_official_export", "development"]
    title: str = Field(min_length=1, max_length=500)
    locator: str = Field(min_length=1, max_length=1500)
    excerpt: str = Field(min_length=1, max_length=4000)

class Product(StrictModel):
    product_id: str = Field(min_length=1, max_length=200)
    model_name: str = Field(min_length=1, max_length=300)
    configuration: str = Field(min_length=1, max_length=500)
    confirmed: bool = False

class Fact(StrictModel):
    fact_id: str = Field(min_length=1, max_length=80)
    product_id: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=2000)
    conditions: str = Field(default="", max_length=1000)
    source: Source

class ImageAsset(StrictModel):
    image_id: str = Field(min_length=1, max_length=80)
    product_id: str = Field(min_length=1, max_length=200)
    access_url: str = Field(min_length=1, max_length=2000)
    caption: str = Field(min_length=1, max_length=1000)
    model_verified: bool = False
    source: Source

class MaterialBundle(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    request_id: str = Field(min_length=1, max_length=100)
    data_mode: Literal["live", "development"]
    status: Literal["ready", "needs_input", "conflict", "unavailable"]
    task: Task
    product: Product | None = None
    facts: list[Fact] = Field(default_factory=list, max_length=100)
    images: list[ImageAsset] = Field(default_factory=list, max_length=30)
    issues: list[str] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def consistent_materials(self):
        fact_ids = [f.fact_id for f in self.facts]
        image_ids = [i.image_id for i in self.images]
        if len(fact_ids) != len(set(fact_ids)) or len(image_ids) != len(set(image_ids)):
            raise ValueError("事实编号与图片编号分别不得重复")
        if self.status == "ready" and (not self.product or not self.product.confirmed or not self.facts):
            raise ValueError("ready必须包含已确认产品与至少一条事实")
        if self.facts or self.images:
            if not self.product:
                raise ValueError("资料必须关联产品身份")
            if any(x.product_id != self.product.product_id for x in [*self.facts, *self.images]):
                raise ValueError("资料中存在与任务产品不一致的事实或图片")
        if self.data_mode == "live" and any(
                x.source.kind not in ("ima", "local_official_export") for x in [*self.facts, *self.images]):
            raise ValueError("真实模式的事实与图片必须声明ima或local_official_export来源；实际来源还需检索适配器验证")
        return self
