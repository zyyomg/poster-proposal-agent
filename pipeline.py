"""Brief → 一页提案 单条流水线（章程 §3 四步）。

用法：
    python pipeline.py b01
    python pipeline.py b01 --no-cache
    python pipeline.py b01 --stage extract      # 只跑到某一步

产物（文件名按章程 §4.3 固定）：
    data/out/b01_extract.json    ① 需求单
    data/out/b01_cases.json      ② 案例匹配
    data/out/b01_ideas.json      ③ 三套方向
    data/out/b01_proposal.md     ④ 一页提案（模板拼装，不经模型）

④ 刻意不用模型：证据、口径、待确认项全部来自前三步的 JSON，
避免最后一步把事实冲淡成一堆「赋能/闭环/提升调性」。
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src.llm import chat_json, load_env  # noqa: E402

OUT = ROOT / "data" / "out"
PROMPTS = ROOT / "prompts"

FIELDS = ["industry", "target", "style", "size", "deliverable", "taboo", "deadline"]
FIELD_CN = {
    "industry": "行业",
    "target": "人群",
    "style": "风格",
    "size": "尺寸",
    "deliverable": "交付物",
    "taboo": "禁忌",
    "deadline": "截止",
}
PLACEHOLDERS = {"未提及", "未知", "待定", "暂无", "无", "null", "None", "-", "—", "n/a", "N/A"}

# 每个 LLM 步骤都追加这一段：把格式要求放在最后，模型对最近的指令最敏感
JSON_SUFFIX = (
    "\n\n严格只输出 JSON：第一个字符必须是 { 或 [，最后一个字符必须是 } 或 ]。"
    "禁止输出任何解释、标题、表格、前言、结语。"
)


def read_brief(bid: str) -> str:
    with (ROOT / "data" / "briefs.csv").open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if row["id"] == bid:
                return row["brief原文"]
    raise RuntimeError(f"briefs.csv 里没有 {bid}")


def read_cases() -> list[dict]:
    with (ROOT / "data" / "cases.csv").open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def clean(v) -> str:
    s = "" if v is None else str(v).strip()
    return "" if s in PLACEHOLDERS else s


# ---------------------------------------------------------------- ① 需求结构化
def stage_extract(bid: str, brief: str, use_cache: bool) -> dict:
    system = (PROMPTS / "extract.md").read_text(encoding="utf-8").split("客户 brief：")[0]
    user = f'客户 brief：\n"""\n{brief}\n"""' + JSON_SUFFIX
    data, cached = chat_json(system, user, stage="extract", brief_id=bid,
                             temperature=0.2, use_cache=use_cache)
    if isinstance(data, list) and data:
        data = data[0]
    if not isinstance(data, dict):
        raise RuntimeError("① 输出不是 JSON 对象")
    out = {k: clean(data.get(k, "")) for k in FIELDS}
    missing = [k for k in FIELDS if not out[k]]
    payload = {"brief_id": bid, "brief": brief, **out, "_missing": missing}
    (OUT / f"{bid}_extract.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[1/4] 需求单  {'缓存' if cached else '新调用'}  留空 {len(missing)}/7 -> {','.join(missing) or '无'}")
    return payload


# ---------------------------------------------------------------- ② 案例匹配
def stage_cases(bid: str, need: dict, use_cache: bool) -> dict:
    cases = read_cases()
    table = "\n".join(
        f"{c['id']} | {c['行业']} | {c['客户诉求']} | {c['风格']} | {c['交付物']} | {c['最终方案摘要']}"
        for c in cases
    )
    need_text = json.dumps({k: need[k] for k in FIELDS}, ensure_ascii=False, indent=2)
    system = (PROMPTS / "cases.md").read_text(encoding="utf-8").split("案例库（id")[0]
    user = (
        f"案例库（id | 行业 | 客户诉求 | 风格 | 交付物 | 最终方案）：\n{table}\n\n"
        f"需求单：\n{need_text}"
    ) + JSON_SUFFIX
    data, cached = chat_json(system, user, stage="cases", brief_id=bid,
                             temperature=0.3, use_cache=use_cache)

    valid = {c["id"] for c in cases}
    picked, seen = [], set()
    for item in data if isinstance(data, list) else []:
        cid = clean(item.get("id", "")) if isinstance(item, dict) else ""
        if cid in valid and cid not in seen:
            seen.add(cid)
            picked.append({"id": cid, "reason": clean(item.get("reason", ""))})
    if len(picked) != 3:
        print(f"      ⚠️ 模型只给出 {len(picked)} 个合法案例，用剩余案例补齐（记为 badcase）")
        for c in cases:
            if len(picked) >= 3:
                break
            if c["id"] not in seen:
                picked.append({"id": c["id"], "reason": "（自动补齐，模型未给出）"})

    payload = {"brief_id": bid, "picks": picked}
    (OUT / f"{bid}_cases.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[2/4] 案例匹配  {'缓存' if cached else '新调用'}  -> " + ", ".join(p["id"] for p in picked))
    return payload


# ---------------------------------------------------------------- ③ 三套方向
def stage_ideas(bid: str, need: dict, cases: dict, use_cache: bool) -> dict:
    case_rows = {c["id"]: c for c in read_cases()}
    case_text = "\n".join(
        f"{p['id']} | {case_rows[p['id']]['行业']} | {case_rows[p['id']]['最终方案摘要']} | 选它的理由：{p['reason']}"
        for p in cases["picks"]
    )
    need_text = json.dumps({k: need[k] for k in FIELDS}, ensure_ascii=False, indent=2)
    system = (PROMPTS / "ideas.md").read_text(encoding="utf-8").split("需求单：")[0]
    base_user = f"需求单：\n{need_text}\n\n参考案例：\n{case_text}"

    hint = ""
    for _ in range(2):
        data, cached = chat_json(
            system, base_user + hint + JSON_SUFFIX, stage="ideas", brief_id=bid,
            temperature=0.9, use_cache=use_cache,
        )
        ds = data.get("directions") if isinstance(data, dict) else None
        norm = [_norm_dir(d, i) for i, d in enumerate(ds or [], 1)]
        if len(norm) == 3 and all(norm):
            payload = {"brief_id": bid, "directions": norm}
            (OUT / f"{bid}_ideas.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"[3/4] 三套方向  {'缓存' if cached else '新调用'}  -> " + " / ".join(d["name"] for d in norm))
            return payload
        # 把结构错误回灌给模型，逼它按 schema 重写一次；
        # 同时删掉这次的缓存，否则这份"能解析但结构错"的响应会被永久命中
        from src.llm import invalidate

        invalidate(system, base_user + hint + JSON_SUFFIX, 0.9)
        hint = (
            "\n\n你上一次输出的结构不合格（套数不对或字段名不对）。"
            "字段名必须严格是 name / headline / visual_prompt / palette / rationale，"
            "palette 必须是 3 个十六进制色值的数组。请重新输出。"
        )
    raise RuntimeError("③ 两次都拿不到合规的三套方向")


def _norm_dir(d, i: int) -> dict | None:
    """容忍模型换字段名（title/文案/配色…），但缺内容就判失败，不编。"""
    if not isinstance(d, dict):
        return None

    def pick(*keys):
        for k in keys:
            if k in d and clean(d[k]):
                return clean(d[k])
        return ""

    name = (
        pick("name", "title", "方向名", "名称", "方案名")
        or pick("mechanism", "手法", "机制")
        or f"方向{i}"
    )
    headline = pick("headline", "文案", "slogan", "主标题", "标题")
    vp = pick("visual_prompt", "prompt", "画面描述", "画面")
    rationale = pick("rationale", "reason", "理由", "为什么")
    palette = d.get("palette") or d.get("colors") or d.get("配色")
    if not (name and headline and vp and rationale):
        return None
    if not isinstance(palette, list) or len(palette) != 3:
        return None
    return {
        "name": name,
        "headline": headline,
        "visual_prompt": vp,
        "palette": [str(x) for x in palette],
        "rationale": rationale,
    }


# ---------------------------------------------------------------- ④ 一页提案
def build_proposal_md(bid: str, need: dict, cases: dict, ideas: dict,
                      images: dict | None = None) -> str:
    """纯函数：把三步结果拼成提案 markdown，不写文件、不打印。
    界面里每次编辑都重新调用它，保证"改了就变"。"""
    case_rows = {c["id"]: c for c in read_cases()}
    missing = [k for k in FIELDS if not need.get(k)]
    ds = ideas["directions"]
    images = images or {}

    L = [f"# 提案初稿 ｜ {bid}", ""]
    L += ["## 一句话理解", "", _one_liner(need), ""]

    L += ["## 需求单", "", "| 字段 | 内容 |", "|---|---|"]
    for k in FIELDS:
        v = need[k] or "**待确认**"
        L.append(f"| {FIELD_CN[k]} | {v} |")
    L.append("")

    if missing:
        L += ["## 需要客户确认（brief 里没说的）", ""]
        asks = {
            "industry": "这个海报是给什么品类/门店做的？",
            "target": "主要给谁看？老客还是新客？",
            "style": "想要什么调性？有没有不能碰的方向？",
            "size": "尺寸和投放位置？竖版还是横版？",
            "deliverable": "交付物是海报，还是也要朋友圈图/物料延展？",
            "taboo": "有没有不能用颜色、元素或说法？",
            "deadline": "什么时候要？发布时间是什么时候？",
        }
        for k in missing:
            L.append(f"- {asks[k]}")
        L.append("")

    L += ["## 三个参考案例", "", "| 案例 | 行业 | 当时怎么做的 | 为什么拿来参考 |", "|---|---|---|---|"]
    for p in cases["picks"]:
        c = case_rows[p["id"]]
        L.append(f"| {p['id']} | {c['行业']} | {c['最终方案摘要']} | {p['reason']} |")
    L.append("")

    L += ["## 三套方向", ""]
    for i, d in enumerate(ds, 1):
        L += [
            f"### 方向 {i}：{d['name']}",
            "",
            f"- **文案**：{d['headline']}",
            f"- **配色**：`{'`  `'.join(d['palette'])}`",
            f"- **画面**：{d['visual_prompt']}",
            f"- **为什么**：{d['rationale']}",
            "",
        ]
        img = images.get(i) or images.get(d["name"])
        if img is not None:
            L += [f"![{d['name']} 草图](img/{Path(img).name})", ""]

    rec = _recommend(need, ds)
    L += ["## 建议先出这一套", "", f"**方向 {rec + 1}：{ds[rec]['name']}** —— {ds[rec]['rationale']}", ""]
    L += [
        "## 下一步",
        "",
        "1. 上面「需要客户确认」的每一条，确认后再进入正式出图；",
        "2. 出图为草图，画面中的文字一律后期排版，不由图像模型生成；",
        "3. 草图 → 客户选向 → 人工精修，成品仍由设计师完成。",
        "",
        "---",
        "",
        "> 本提案由原型自动生成，用于验证「brief → 初稿」的压缩效果，"
        "不替代设计师判断。素材均为自造脱敏样本。",
    ]
    return "\n".join(L)


def stage_proposal(bid: str, need: dict, cases: dict, ideas: dict,
                   images: dict | None = None) -> str:
    text = build_proposal_md(bid, need, cases, ideas, images)
    (OUT / f"{bid}_proposal.md").write_text(text, encoding="utf-8")
    missing = [k for k in FIELDS if not need.get(k)]
    print(f"[4/4] 提案  -> data/out/{bid}_proposal.md（{len(missing)} 个待确认项）")
    return text


def _one_liner(need: dict) -> str:
    parts = []
    if need["industry"]:
        parts.append(f"给{need['industry']}")
    if need["target"]:
        parts.append(f"面向{need['target']}")
    if need["style"]:
        parts.append(f"做一版{need['style']}的")
    parts.append(need["deliverable"] or "海报")
    if need["deadline"]:
        parts.append(f"，{need['deadline']}要")
    if need["taboo"]:
        parts.append(f"，注意{need['taboo']}")
    s = "".join(parts)
    if not need["industry"] and not need["style"]:
        s += "；客户几乎没给信息，以下全部按「待确认」处理，不猜品类"
    return "客户想" + s + "。"


def _recommend(need: dict, ds: list[dict]) -> int:
    """优先选视觉机制与 brief 信息量匹配的那套：信息越少，越该用「符号化」而不是「叙事」。"""
    info = sum(1 for k in FIELDS if need[k])
    if info <= 2:
        for i, d in enumerate(ds):
            if any(w in d["visual_prompt"] for w in ("剪影", "符号", "字形", "几何", "大面积")):
                return i
    return 0


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("brief_id")
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--images", type=int, default=0,
                    help="给前 N 套方向出草图（每张 0.2 元，缓存命中不计费）")
    ap.add_argument("--no-image-cache", action="store_true")
    ap.add_argument("--stage", choices=["extract", "cases", "ideas", "proposal"], default=None)
    args = ap.parse_args()

    load_env()
    OUT.mkdir(parents=True, exist_ok=True)
    bid = args.brief_id
    brief = read_brief(bid)
    print(f"brief {bid}: {brief}")
    print("-" * 60)

    need = stage_extract(bid, brief, not args.no_cache)
    if args.stage == "extract":
        return 0
    cases = stage_cases(bid, need, not args.no_cache)
    if args.stage == "cases":
        return 0
    ideas = stage_ideas(bid, need, cases, not args.no_cache)
    if args.stage == "ideas":
        return 0

    images: dict[str, Path] = {}
    if args.images:
        from src import images as img_mod

        before = img_mod.request_count()
        spent = 0.0
        for d in ideas["directions"][: args.images]:
            try:
                r = img_mod.generate(d["visual_prompt"], use_cache=not args.no_image_cache)
            except Exception as e:
                print(f"      出图失败（{d['name']}）：{e}")
                continue
            images[d["name"]] = r["path"]
            spent += r["cost"]
            print(f"      {'缓存' if r['cached'] else '新生成'} {d['name']} -> {r['path'].name}  {r['note']}")
        delta = img_mod.request_count() - before
        print(f"      出图请求 {delta} 次，花费 {spent:.2f} 元")

    stage_proposal(bid, need, cases, ideas, images)
    print("-" * 60)
    print("完成。用量见 data/out/usage.csv（缓存命中不产生调用）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
