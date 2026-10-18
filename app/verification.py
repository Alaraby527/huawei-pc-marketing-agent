"""Deterministic pre-generation checks. This is NOT semantic fact verification."""
from .contracts import MaterialBundle
from .platforms import PLATFORM_RULES

def check_materials(bundle: MaterialBundle) -> dict:
    blockers = []
    if bundle.data_mode != "live":
        blockers.append("开发样例仅用于联调，不能作为真实运行结果或发布依据")
    if bundle.status != "ready":
        blockers.append(f"材料状态为{bundle.status}，需要补充或人工确认")
    if not bundle.product or not bundle.product.confirmed:
        blockers.append("产品身份或配置尚未确认")
    if not bundle.facts:
        blockers.append("没有可引用的产品事实")
    usable_images = [i for i in bundle.images if i.model_verified and i.source.kind in ("ima", "local_official_export")]
    rule = PLATFORM_RULES[bundle.task.platform.value]
    if len(usable_images) < rule["image_min"]:
        blockers.append(f"已核实ima图片不足：{len(usable_images)}张，需要至少{rule['image_min']}张")
    return {
        "schema_valid": True,
        "prechecks_passed": not blockers,
        "can_publish": False,
        "reason": "此接口只检查数据结构、身份对应与素材完整度；没有验证来源真实性或生成成稿",
        "usable_image_count": len(usable_images),
        "platform_rule": rule,
        "blockers": blockers,
        "upstream_issues": bundle.issues,
    }
