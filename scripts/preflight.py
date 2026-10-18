"""Usage: python scripts/preflight.py https://your-space-runtime-host"""
import json, sys, urllib.request
base=(sys.argv[1] if len(sys.argv)>1 else "http://127.0.0.1:7860").rstrip("/")
def get(path):
    with urllib.request.urlopen(base+path,timeout=15) as r:
        return json.load(r)
try:
    health=get("/healthz")
    schema=get("/api/v1/contracts/materials")
    readiness=get("/api/v1/readiness")
    assert health["status"]=="ok"
    assert "properties" in schema
    print("DEPLOYMENT CHECK: PASS — 页面后端和材料规范接口可访问")
    print("BUSINESS CHECK:", "PASS" if readiness["business_ready"] else "NOT READY — 尚未接入真实业务")
    print(json.dumps(readiness,ensure_ascii=False,indent=2))
except Exception as exc:
    print("DEPLOYMENT CHECK: FAIL —",type(exc).__name__)
    sys.exit(1)
