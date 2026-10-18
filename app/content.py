"""Part B integration boundary. Implement platform strategy, draft and audit here."""
from .contracts import MaterialBundle
from .verification import check_materials

def compose(bundle: MaterialBundle) -> dict:
    report = check_materials(bundle)
    return {
        "status": "integration_pending",
        "request_id": bundle.request_id,
        "message": "已建立接口；平台策略、真实生成和成稿核验仍需开发接入。",
        "precheck": report,
        "can_publish": False,
    }
