"""万相图像生成 + 本地文件缓存（章程 §2.1 / §2.2）。

缓存 key = md5(模型 + 尺寸 + 反向提示词 + 正向提示词)[:12]，落盘到 data/out/img/<key>.png。
缓存命中时不发任何网络请求——这是 Day 5 的验收点：第二次运行不产生新请求。

记录：data/out/img/manifest.json，每次真实请求追加一条（含 task_id / request_id），
     用来核对「第一次 N 次请求、第二次 0 次」。

成本：0.2 元/张（北京），失败调用不计费。
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / ".env"
IMG_DIR = ROOT / "data" / "out" / "img"
MANIFEST = IMG_DIR / "manifest.json"

MODEL = "wan2.7-image"
# 选型变更记录（v1.1，2026-10-31）：
#   v1.0 用 wan2.6-image（0.2 元/张，无免费额度），12 张共 2.40 元；
#   v1.1 切 wan2.7-image：有 50 张免费额度（到期 2027-01-29），且输出精确 1080x1440
#   （wan2.6 会被 16 的倍数取整成 1072x1440）。
# 这不是"中途换技术栈"，是拿到实测账单后的选型更新，已在 docs/day5_images.md 留痕。
# 业务空间专属域名优先（官方建议迁移到它），旧的 dashscope 域名做兜底
BASES = [
    "https://ws-xvong7tk2by7l2v8.cn-beijing.maas.aliyuncs.com",
    "https://dashscope.aliyuncs.com",
]
DEFAULT_SIZE = "1080*1440"
# 上一次实测发现：即便提示词写了"不要文字"，模型仍会硬塞乱码英文。
# 所以反向提示词是必填项，不是可选优化。
NEGATIVE = "文字,字母,数字,水印,logo,品牌标识,签名,乱码,拼写错误,副标题,排版文字"
PRICE_PER_IMAGE = 0.2
# 计价表：wan2.7 系列有免费额度（各 50 张，2027-01-29 到期），额度内不计费。
# 额度用尽后按量价需回控制台核实，本表不含猜测值——查不到就填 None。
PRICES = {
    "wan2.6-image": 0.2,
    "wan2.7-image": 0.0,
    "wan2.7-image-pro": 0.0,
}
FREE_QUOTA_TOTAL = {"wan2.7-image": 50, "wan2.7-image-pro": 50}


def _key() -> str:
    for line in ENV.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s.startswith("DASHSCOPE_API_KEY="):
            return s.split("=", 1)[1].strip()
    return ""


def _cache_key(prompt: str, size: str, negative: str, model: str = MODEL) -> str:
    raw = f"{model}|{size}|{negative}|{prompt}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:12]


def _manifest_load() -> dict:
    if MANIFEST.exists():
        return json.loads(MANIFEST.read_text(encoding="utf-8"))
    return {}


def _manifest_add(key: str, entry: dict) -> None:
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    m = _manifest_load()
    m[key] = entry
    MANIFEST.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")


def _post(url: str, payload: dict, key: str) -> tuple[int, dict | str]:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "X-DashScope-Async": "enable",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        body = e.read(2000).decode("utf-8", "replace")
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, body
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}"


def _get(url: str, key: str) -> tuple[int, dict | str]:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return e.code, e.read(600).decode("utf-8", "replace")
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}"


def generate(prompt: str, *, size: str = DEFAULT_SIZE, negative: str = NEGATIVE,
             use_cache: bool = True, poll_seconds: int = 240,
             model: str = MODEL) -> dict:
    """返回 {path, cached, cost, note}。缓存命中时不联网。"""
    key = _cache_key(prompt, size, negative, model)
    dst = IMG_DIR / f"{key}.png"

    if use_cache and dst.exists():
        m = _manifest_load().get(key, {})
        return {"path": dst, "cached": True, "cost": 0.0,
                "note": f"缓存命中（原 task {m.get('task_id', '?')}）"}

    token = _key()
    if not token:
        raise SystemExit("DASHSCOPE_API_KEY 未设置")

    payload = {
        "model": model,
        "input": {"messages": [{"role": "user", "content": [{"text": prompt}]}]},
        "parameters": {
            "enable_interleave": True,
            "size": size,
            "max_images": 1,
            "watermark": False,
            "prompt_extend": False,
            "negative_prompt": negative,
        },
    }

    last_err = ""
    task_id = None
    used = ""
    for base in BASES:
        code, body = _post(f"{base}/api/v1/services/aigc/image-generation/generation", payload, token)
        if code == 200 and isinstance(body, dict):
            task_id = (body.get("output") or {}).get("task_id")
            used = base
            break
        last_err = f"HTTP {code} {str(body)[:200]}"
    if not task_id:
        raise RuntimeError(f"创建任务失败：{last_err}")

    deadline = time.time() + poll_seconds
    status = None
    res: dict = {}
    while time.time() < deadline:
        time.sleep(5)
        code, res = _get(f"{used}/api/v1/tasks/{task_id}", token)
        if code != 200 or not isinstance(res, dict):
            continue
        status = (res.get("output") or {}).get("task_status")
        if status in ("SUCCEEDED", "FAILED", "CANCELED", "UNKNOWN"):
            break
    if status != "SUCCEEDED":
        raise RuntimeError(f"任务未成功：status={status} {str(res)[:300]}")

    out = res.get("output") or {}
    usage = res.get("usage") or {}
    content = ((out.get("choices") or [{}])[0].get("message") or {}).get("content", [])
    urls = [c["image"] for c in content if c.get("type") == "image" and c.get("image")]
    if not urls:
        raise RuntimeError("任务成功但没有返回图片 URL")

    IMG_DIR.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(urls[0], timeout=180) as r:
        dst.write_bytes(r.read())

    images = int(usage.get("image_count") or 1)
    _manifest_add(key, {
        "task_id": task_id,
        "request_id": res.get("request_id", ""),
        "model": model,
        "size": usage.get("size") or size,
        "image_count": images,
        "source": "api",
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
    })
    unit = PRICES.get(model)
    cost = (unit * images) if unit is not None else None
    note = f"新生成 task {task_id}"
    if unit == 0.0:
        used = sum(
            v.get("image_count", 0)
            for v in _manifest_load().values()
            if v.get("model") == model and v.get("source") == "api"
        )
        total = FREE_QUOTA_TOTAL.get(model)
        note += f"（免费额度，已用 {used}/{total}）"
    return {"path": dst, "cached": False, "cost": cost, "note": note}


def request_count() -> int:
    """manifest 里 source=api 的条目数 = 历史真实请求数。"""
    return sum(1 for v in _manifest_load().values() if v.get("source") == "api")
