"""LLM 调用层：读密钥、文件缓存、JSON 解析、用量记录。

设计约束（章程 §2.1）：
- 模型固定 deepseek-flash，base_url 固定 https://api.deepseek.com
- 缓存 key = md5(prompt)[:12]，同一 prompt 不重复请求
- 不在这里做任何重试以外的"聪明事"，保持可预测
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "data" / "cache"
USAGE_CSV = ROOT / "data" / "out" / "usage.csv"

MODEL = "deepseek-flash"
BASE_URL = "https://api.deepseek.com"


def load_env(path: Path | None = None) -> dict[str, str]:
    p = path or (ROOT / ".env")
    out: dict[str, str] = {}
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _client():
    from openai import OpenAI

    key = load_env().get("DEEPSEEK_API_KEY", "")
    if not key:
        raise RuntimeError("DEEPSEEK_API_KEY 未设置，检查 .env")
    return OpenAI(api_key=key, base_url=BASE_URL)


def _key_of(system: str, user: str, temperature: float) -> str:
    raw = f"{MODEL}\x00{temperature}\x00{system}\x00{user}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:12]


def _log_usage(brief_id: str, stage: str, usage: Any, cached: bool) -> None:
    USAGE_CSV.parent.mkdir(parents=True, exist_ok=True)
    new = not USAGE_CSV.exists()
    with USAGE_CSV.open("a", encoding="utf-8", newline="") as f:
        if new:
            f.write("ts,brief_id,stage,model,prompt_tokens,completion_tokens,total_tokens,cached\n")
        f.write(
            f"{time.strftime('%Y-%m-%d %H:%M:%S')},{brief_id},{stage},{MODEL},"
            f"{getattr(usage, 'prompt_tokens', '')},{getattr(usage, 'completion_tokens', '')},"
            f"{getattr(usage, 'total_tokens', '')},{int(cached)}\n"
        )


def chat(
    system: str,
    user: str,
    *,
    stage: str,
    brief_id: str,
    temperature: float = 0.6,
    use_cache: bool = True,
    json_mode: bool = False,
) -> tuple[str, bool]:
    """返回 (模型输出文本, 是否命中缓存)。"""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    ck = _key_of(system, user, temperature)
    cf = CACHE_DIR / f"{ck}.json"

    if use_cache and cf.exists():
        data = json.loads(cf.read_text(encoding="utf-8"))
        _log_usage(brief_id, stage, type("U", (), {})(), True)
        return data["text"], True

    kwargs = {}
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    try:
        resp = _client().chat.completions.create(
            model=MODEL,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=temperature,
            **kwargs,
        )
    except Exception:
        if not json_mode:
            raise
        # 该模型/该版本不支持 json_object 时退回普通模式
        resp = _client().chat.completions.create(
            model=MODEL,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=temperature,
        )
    text = resp.choices[0].message.content or ""
    cf.write_text(
        json.dumps(
            {"text": text, "usage": getattr(resp.usage, "model_dump", lambda: {})()},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    _log_usage(brief_id, stage, resp.usage, False)
    return text, False


def _sanitize(s: str) -> str:
    """清掉会破坏 JSON 的字符：控制符、私用区、非 BMP 的散字符。"""
    out = []
    for ch in s:
        o = ord(ch)
        if o < 0x20 and ch not in "\n\r\t":
            continue
        if 0xE000 <= o <= 0xF8FF:      # 私用区，模型偶尔吐这种
            continue
        if o > 0xFFFF:                  # 非 BMP，JSON 里合法但常伴随乱码
            continue
        out.append(ch)
    return "".join(out)


def parse_json(text: str) -> Any:
    """从模型输出里取出 JSON。容忍 ```json 围栏、前后废话、损坏字符。"""
    s = text.strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)

    candidates = [s, _sanitize(s)]
    for cand in candidates:
        try:
            return json.loads(cand)
        except json.JSONDecodeError:
            pass
        for opener, closer in (("{", "}"), ("[", "]")):
            i, j = cand.find(opener), cand.rfind(closer)
            if i != -1 and j > i:
                try:
                    return json.loads(cand[i : j + 1])
                except json.JSONDecodeError:
                    continue
    raise ValueError(f"无法解析 JSON，原文前 300 字：\n{text[:300]}")


def invalidate(system: str, user: str, temperature: float) -> bool:
    """删掉某次调用的缓存。用于"能解析但结构不合规"的脏数据——
    这类数据不删就会一直命中，让同一个 brief 永远跑不通。"""
    cf = CACHE_DIR / f"{_key_of(system, user, temperature)}.json"
    if cf.exists():
        cf.unlink()
        return True
    return False


def chat_json(system: str, user: str, *, stage: str, brief_id: str, temperature: float = 0.6,
              attempts: int = 3, use_cache: bool = True) -> tuple[Any, bool]:
    last = None
    for _ in range(attempts):
        text, cached = chat(
            system,
            user,
            stage=stage,
            brief_id=brief_id,
            temperature=temperature,
            use_cache=use_cache,
            json_mode=True,
        )
        try:
            return parse_json(text), cached
        except ValueError as e:
            last = e
            if cached:
                # 缓存里是坏内容：删掉，让下一轮真正重新请求
                (CACHE_DIR / f"{_key_of(system, user, temperature)}.json").unlink(missing_ok=True)
            continue
    raise RuntimeError(f"[{stage}] JSON 解析失败：{last}")
