"""Key probe: reads .env, never prints plaintext, makes no billable calls.

Usage: python tools/probe_keys.py

Per provider it does two things:
1. GET /models  -- lists model ids if the key is accepted.
2. POST a request naming a model that cannot exist -- a 400/404 means the key passed
   auth (the server reached the model lookup), a 401/403 means the key is rejected.
   Nothing is generated, so nothing is billed.

Output is ASCII on purpose: the Windows console here is cp936 and would garble CJK.
"""

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / ".env"

# name -> (models_url, chat_url)
TARGETS = {
    "DASHSCOPE_API_KEY": (
        "https://dashscope.aliyuncs.com/compatible-mode/v1/models",
        "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
    ),
    "DEEPSEEK_API_KEY": (
        "https://api.deepseek.com/models",
        "https://api.deepseek.com/chat/completions",
    ),
}

PROBE_MODEL = "__probe_model_that_cannot_exist__"


def load_env(path: Path) -> dict:
    data = {}
    if not path.exists():
        return data
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        data[k.strip()] = v.strip().strip('"').strip("'")
    return data


def mask(v: str) -> str:
    if len(v) <= 8:
        return "*" * len(v)
    return f"{v[:6]}...{v[-2:]}(len={len(v)})"


def get(url: str, key: str) -> str:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            body = r.read(6000).decode("utf-8", "replace")
            ids = []
            try:
                ids = [m.get("id") for m in json.loads(body).get("data", [])]
            except Exception:
                pass
            if not ids:
                return f"OK {r.status} but 0 models listed (inconclusive)"
            return f"OK {r.status} models={len(ids)} {ids[:6]}"
    except urllib.error.HTTPError as e:
        return f"HTTP {e.code} {e.reason}"
    except Exception as e:
        return f"FAIL {type(e).__name__}: {e}"


def auth(url: str, key: str) -> str:
    payload = {
        "model": PROBE_MODEL,
        "messages": [{"role": "user", "content": "x"}],
        "max_tokens": 1,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return f"UNEXPECTED {r.status} (a fake model was accepted?)"
    except urllib.error.HTTPError as e:
        body = e.read(600).decode("utf-8", "replace")
        if e.code in (400, 404, 422):
            return f"AUTH OK ({e.code}: model rejected as expected)"
        if e.code in (401, 403):
            return f"AUTH FAILED ({e.code}) body={body[:200]}"
        return f"HTTP {e.code} body={body[:200]}"
    except Exception as e:
        return f"FAIL {type(e).__name__}: {e}"


def main() -> int:
    env = load_env(ENV)
    bad = 0
    for name, (murl, curl) in TARGETS.items():
        val = env.get(name, "")
        if not val:
            print(f"[ ] {name}: empty - not filled yet")
            bad += 1
            continue
        if not val.startswith("sk-"):
            print(f"[!] {name}: {mask(val)} prefix is not sk-")
            bad += 1
            continue
        print(f"[>] {name}: {mask(val)}")
        print(f"    models -> {get(murl, val)}")
        print(f"    auth   -> {auth(curl, val)}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
