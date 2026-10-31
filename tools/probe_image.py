"""Real wan2.6-image / wan2.7-image probe against the workspace-scoped domain.

The console shows the key is bound to a workspace domain
(https://<workspace>.cn-beijing.maas.aliyuncs.com), not to the legacy
dashscope.aliyuncs.com -- that mismatch is what produced 401 InvalidApiKey.

Cost: 0.2 CNY per successfully generated image. Rejected/failed calls are not billed.

Usage:
    python tools/probe_image.py              # wan2.6-image on workspace domain
    python tools/probe_image.py wan2.7-image # another model, same path
"""

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / ".env"
IMG_DIR = ROOT / "data" / "out" / "img"

WORKSPACE = "ws-xvong7tk2by7l2v8"
BASES = [
    f"https://{WORKSPACE}.cn-beijing.maas.aliyuncs.com",
    "https://dashscope.aliyuncs.com",
]

MODEL = sys.argv[1] if len(sys.argv) > 1 else "wan2.6-image"

PROMPT = (
    "一张竖版活动海报的主视觉空镜：深黑背景的影棚，"
    "一件未明确品类的新品以剪影形态半没入灰雾，一束冷白侧光斜切过画面，"
    "左上留出大片纯色负空间作为标题区，画面中不出现任何文字，高级感，极简，电影感打光"
)


def load_key() -> str:
    for line in ENV.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s.startswith("DASHSCOPE_API_KEY="):
            return s.split("=", 1)[1].strip()
    return ""


def call(url: str, key: str, payload: dict | None = None) -> tuple[int, dict | str]:
    headers = {"Authorization": f"Bearer {key}"}
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        headers["X-DashScope-Async"] = "enable"
        data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        body = e.read(3000).decode("utf-8", "replace")
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, body
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}"


def brief(x) -> str:
    if isinstance(x, dict):
        return f"code={x.get('code')} message={x.get('message')}"
    return str(x)[:300]


def main() -> int:
    key = load_key()
    if not key:
        print("DASHSCOPE_API_KEY empty")
        return 1
    print(f"model={MODEL} key={key[:6]}...len={len(key)}")

    payload = {
        "model": MODEL,
        "input": {"messages": [{"role": "user", "content": [{"text": PROMPT}]}]},
        "parameters": {
            "enable_interleave": True,
            "size": "720*1280",
            "max_images": 1,
            "watermark": False,
            "prompt_extend": False,
        },
    }

    task_id = None
    used = None
    for base in BASES:
        code, body = call(f"{base}/api/v1/services/aigc/image-generation/generation", key, payload)
        tag = "workspace" if base == BASES[0] else "legacy"
        print(f"[{tag}] create -> HTTP {code} {brief(body)}")
        if code == 200:
            task_id = (body.get("output") or {}).get("task_id")
            used = base
            break

    if not task_id:
        print("RESULT: no accepted endpoint (nothing billed)")
        return 2

    query = f"{used}/api/v1/tasks/{task_id}"
    deadline = time.time() + 180
    status = None
    while time.time() < deadline:
        time.sleep(5)
        code, res = call(query, key)
        if code != 200:
            print(f"query -> HTTP {code} {brief(res)}")
            return 3
        status = (res.get("output") or {}).get("task_status")
        print(f"  status={status}")
        if status in ("SUCCEEDED", "FAILED", "CANCELED", "UNKNOWN"):
            break

    if status != "SUCCEEDED":
        print("RESULT: task failed ->", brief(res.get("output") if isinstance(res, dict) else res)[:300])
        print(json.dumps(res, ensure_ascii=True)[:600])
        return 3

    content = ((res.get("output") or {}).get("choices") or [{}])[0].get("message", {}).get("content", [])
    urls = [c["image"] for c in content if c.get("type") == "image" and c.get("image")]
    usage = res.get("usage") or {}
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    for i, u in enumerate(urls, 1):
        dst = IMG_DIR / f"probe_{MODEL.replace('.', '')}_{int(time.time())}_{i}.png"
        with urllib.request.urlopen(u, timeout=120) as r:
            dst.write_bytes(r.read())
        print(f"saved -> {dst} ({dst.stat().st_size} bytes)")
    print(f"RESULT: OK image_count={usage.get('image_count')} size={usage.get('size')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
