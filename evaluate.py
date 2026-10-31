"""批量跑 + 评测。

    python evaluate.py --run            # 30 条全跑一遍（已有缓存的不产生调用）
    python evaluate.py --score          # 只评测已有产物
    python evaluate.py --run --score    # 先跑再评
    python evaluate.py --ids b01,b02    # 只跑指定条
    python evaluate.py --tag v3         # 只决定输出文件名 eval_v3.csv

注意：--tag 不切换产物版本。评的是哪一版，取决于 data/out 里当前是哪一版产物。
      跑完 v3 再想评 v2，必须先恢复 v2 的产物，否则会把 v3 的分数写成 v2。

评测口径（Day 3 定下的等价规则，见 docs/day3_pipeline.md）：
- 归一化：去空白标点、全角转半角
- 禁忌：`不要X` / `别X` / `太X` 视为同义
- 行业：允许上下位（母婴 ⊃ 奶粉），命中记「对（粒度不同）」
- 风格：标注值 ⊆ 模型值 算对；标注为空而模型给了值算错
- 其余字段：互相包含即算对

三个指标分开报：
- 总准确率（分母 210，对齐章程 §5 的 85%）
- 非空字段准确率（分母 = 标注非空的点数，真正体现抽取能力）
- 留空正确率（分母 = 标注为空的点数，体现抗幻觉）
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data" / "out"
FIELDS = ["industry", "target", "style", "size", "deliverable", "taboo", "deadline"]
CN = dict(zip(FIELDS, ["行业", "人群", "风格", "尺寸", "交付物", "禁忌", "截止"]))

# 行业下位词：标注用的是"桶"，模型常直接给具体业态（烧烤店/美甲店/猫舍）。
# 命中即算对，但记为「粒度不同」——它虽算对，却会影响下游案例匹配。
HINTS = {
    "餐饮": ["烧烤", "火锅", "咖啡", "手冲", "烘焙", "奶茶", "下午茶", "餐", "饭"],
    "零售快消": ["便利店", "酱料", "服饰", "零食", "快消", "零售"],
    "健身运动": ["健身", "瑜伽", "拳击", "游泳", "运动"],
    "母婴亲子": ["母婴", "奶粉", "纸尿裤", "玩具", "婴儿", "亲子"],
    "汽车": ["汽车", "汽修", "二手车", "洗车", "车源", "车辆"],
    "家居服务": ["家装", "整装", "家政", "保洁", "月嫂", "家居"],
    "美业个护": ["美甲", "皮肤管理", "美容", "美业", "个护"],
    "文旅": ["景区", "民宿", "酒店", "文旅", "旅游"],
    "教育": ["教育", "培训", "班", "课", "考研", "数学", "英语"],
    "医疗健康": ["口腔", "种植牙", "医疗", "牙科", "医院"],
    "宠物": ["宠物", "猫舍", "猫", "狗"],
    "3C科技": ["耳机", "手机", "数码", "3c", "科技"],
    "食品调味": ["食品", "辣椒", "调味", "酱料", "酱"],
}
PUNCT = " \t\r\n，。、；：！？·—-_,.;:!?/\\|()（）【】[]<>《》\"'“”‘’"


def norm(s: str) -> str:
    s = (s or "").strip().lower()
    s = s.replace("，", ",").replace("、", ",").replace("；", ",")
    for ch in PUNCT:
        s = s.replace(ch, "")
    return s


def strip_neg(s: str) -> str:
    for w in ("不要", "不能", "别", "避免", "太", "整"):
        s = s.replace(w, "")
    return s


def core(s: str) -> str:
    """去掉标注里「（说明）」形式的括注，只留主干：'高级（贵气）' -> '高级'。"""
    for sep in ("（", "("):
        if sep in s:
            s = s.split(sep)[0]
    return s


def tokens(s: str) -> set[str]:
    parts = re.split(r"[、,，;；\s]+", core(s))
    return {norm(t) for t in parts if norm(t)}


def like(a: str, b: str) -> bool:
    """词根等价：'可爱' ≈ '可爱点'，'狠' ≈ '狠一点'，'放心' ≈ '让人放心'。"""
    if a == b:
        return True
    if not a or not b:
        return False
    return a in b or b in a


def judge(field: str, gold: str, pred: str) -> tuple[bool, str]:
    g, p = norm(gold), norm(pred)
    if g == p:
        return True, ""
    if not g:
        return False, "该留空却填了"
    if not p:
        return False, "该填却留空"

    if field == "taboo":
        g2, p2 = strip_neg(g), strip_neg(p)
        if g2 == p2 or g2 in p2 or p2 in g2:
            return True, ""
        return False, "禁忌不等价"

    if field == "industry":
        def buckets(s: str) -> set[str]:
            s = norm(core(s))
            out = set()
            for key, hints in HINTS.items():
                if norm(key) in s or s in norm(key) or any(norm(h) in s for h in hints):
                    out.add(key)
            return out

        if buckets(gold) & buckets(pred):
            return True, "粒度不同"
        if g in p or p in g:
            return True, "粒度不同"
        return False, "行业不匹配"

    if field == "style":
        gs, ps = tokens(gold), tokens(pred)
        if not gs:
            return False, "该留空却填了"
        unmatched = {t for t in gs if not any(like(t, q) for q in ps)}
        if unmatched:
            return False, f"风格不等价（缺 {','.join(sorted(unmatched))}）"
        extra = {q for q in ps if not any(like(q, t) for t in gs)}
        if any(any(ch in q for ch in "不别非忌") for q in extra):
            return False, f"风格里混入禁忌词（{','.join(sorted(extra))}）"
        return True, ""

    if g in p or p in g:
        return True, ""
    return False, f"{CN[field]}不等价"


def run_all(ids: list[str]) -> list[tuple[str, int, float]]:
    results = []
    for i, bid in enumerate(ids, 1):
        t0 = time.time()
        print(f"[{i}/{len(ids)}] {bid}", flush=True)
        try:
            r = subprocess.run(
                [sys.executable, str(ROOT / "pipeline.py"), bid],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=600, cwd=str(ROOT),
            )
            rc = r.returncode
            if rc != 0:
                print("   FAILED:", (r.stderr or r.stdout).strip().splitlines()[-1:], flush=True)
        except subprocess.TimeoutExpired:
            rc = -1
            print("   TIMEOUT", flush=True)
        results.append((bid, rc, time.time() - t0))
    return results


def load_gold() -> dict[str, dict]:
    with (ROOT / "data" / "briefs.csv").open(encoding="utf-8", newline="") as f:
        return {row["id"]: row for row in csv.DictReader(f)}


def score(ids: list[str], tag: str = "v1") -> int:
    gold = load_gold()
    rows, missing_outputs = [], []
    for bid in ids:
        f = OUT / f"{bid}_extract.json"
        if not f.exists():
            missing_outputs.append(bid)
            continue
        pred = json.loads(f.read_text(encoding="utf-8"))
        for field in FIELDS:
            g = gold[bid][CN[field]]
            p = pred.get(field, "")
            ok, note = judge(field, g, p)
            rows.append({"brief": bid, "field": field, "gold": g, "pred": p,
                         "verdict": "对" if ok else "错", "note": note})

    with (OUT / f"eval_{tag}.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["brief", "field", "gold", "pred", "verdict", "note"])
        w.writeheader()
        w.writerows(rows)

    tot = len(rows)
    hit = sum(1 for r in rows if r["verdict"] == "对")
    ne = [r for r in rows if r["gold"].strip()]
    bl = [r for r in rows if not r["gold"].strip()]
    ne_hit = sum(1 for r in ne if r["verdict"] == "对")
    bl_hit = sum(1 for r in bl if r["verdict"] == "对")
    granular = sum(1 for r in rows if r["note"] == "粒度不同")

    print()
    print("=" * 58)
    print(f"判定点 {tot}（{len(ids) - len(missing_outputs)} 条 brief）")
    print(f"总准确率        {hit}/{tot} = {hit/tot:.1%}")
    print(f"非空字段准确率  {ne_hit}/{len(ne)} = {ne_hit/len(ne):.1%}")
    print(f"留空正确率      {bl_hit}/{len(bl)} = {bl_hit/len(bl):.1%}")
    print(f"粒度不同（算对）{granular} 处")
    if missing_outputs:
        print(f"缺产物: {missing_outputs}")
    print()
    print("错的部分：")
    bad = [r for r in rows if r["verdict"] == "错"]
    for r in bad:
        print(f"  {r['brief']}.{r['field']}: 标注='{r['gold']}' 模型='{r['pred']}' ({r['note']})")
    print(f"共 {len(bad)} 处错误，明细见 data/out/eval_{tag}.csv")
    return 0


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--score", action="store_true")
    ap.add_argument("--ids", default="")
    ap.add_argument("--tag", default="v1", help="评测版本号，决定输出文件名 eval_<tag>.csv")
    a = ap.parse_args()
    ids = a.ids.split(",") if a.ids else [f"b{i:02d}" for i in range(1, 31)]
    if a.run:
        res = run_all(ids)
        bad = [r for r in res if r[1] != 0]
        print(f"\n跑完 {len(res)} 条，失败 {len(bad)} 条，总耗时 {sum(r[2] for r in res):.0f}s")
    if a.score or not a.run:
        score(ids, a.tag)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
