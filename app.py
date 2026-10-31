"""Streamlit 单页：brief -> 一页提案（可编辑工作台）。

关键设计：
- 结果不是"只读展示"，而是可编辑表单：需求单 / 案例理由 / 文案 / 提示词 / 配色都能改
- 一页提案每次都由当前编辑值重新拼装（`pipeline.build_proposal_md` 是纯函数），改一个字就立刻反映
- 配色用 st.color_picker，点开是浏览器原生取色器（Chrome 下带吸管）

启动：
    python -m streamlit run app.py
    （或双击 启动界面.bat）
"""

from __future__ import annotations

import csv
import json
import sys
import time
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import pipeline as P  # noqa: E402
from src import images as IMG  # noqa: E402

st.set_page_config(page_title="Brief → 提案初稿", page_icon="🎯", layout="wide")

FIELD_CN = P.FIELD_CN
BID = "live"


@st.cache_data
def load_samples() -> dict[str, str]:
    out = {}
    with (ROOT / "data" / "briefs.csv").open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            out[f"{row['id']}｜{row['brief原文'][:18]}…"] = row["brief原文"]
    return out


@st.cache_data
def all_cases() -> dict[str, dict]:
    with (ROOT / "data" / "cases.csv").open(encoding="utf-8", newline="") as f:
        return {r["id"]: r for r in csv.DictReader(f)}


def quota_left() -> tuple[int, int]:
    p = ROOT / "data" / "out" / "img" / "manifest.json"
    used = 0
    if p.exists():
        m = json.loads(p.read_text(encoding="utf-8"))
        used = sum(
            v.get("image_count", 0)
            for v in m.values()
            if str(v.get("model", "")).startswith("wan2.7") and v.get("source") == "api"
        )
    return used, IMG.FREE_QUOTA_TOTAL.get("wan2.7-image", 50)


def seed_need(need: dict) -> None:
    for k in P.FIELDS:
        st.session_state[f"need_{k}"] = need.get(k, "")


def seed_cases(cases: dict) -> None:
    for i, p in enumerate(cases["picks"]):
        st.session_state[f"case{i}_id"] = p["id"]
        st.session_state[f"case{i}_reason"] = p["reason"]


def seed_ideas(ideas: dict) -> None:
    for i, d in enumerate(ideas["directions"]):
        st.session_state[f"d{i}_name"] = d["name"]
        st.session_state[f"d{i}_headline"] = d["headline"]
        st.session_state[f"d{i}_vp"] = d["visual_prompt"]
        st.session_state[f"d{i}_why"] = d["rationale"]
        for j, c in enumerate(d["palette"][:3]):
            st.session_state[f"d{i}_c{j}"] = c


def collect() -> tuple[dict, dict, dict]:
    """从控件状态收集当前值 —— 提案就用它拼，所以改了就变。"""
    need = {k: str(st.session_state.get(f"need_{k}", "") or "").strip() for k in P.FIELDS}
    need["_missing"] = [k for k in P.FIELDS if not need[k]]
    cases = {"brief_id": BID, "picks": [
        {"id": st.session_state.get(f"case{i}_id", "c01"),
         "reason": st.session_state.get(f"case{i}_reason", "")}
        for i in range(3)
    ]}
    ideas = {"brief_id": BID, "directions": [
        {"name": st.session_state.get(f"d{i}_name", ""),
         "headline": st.session_state.get(f"d{i}_headline", ""),
         "visual_prompt": st.session_state.get(f"d{i}_vp", ""),
         "palette": [st.session_state.get(f"d{i}_c{j}", "#000000") for j in range(3)],
         "rationale": st.session_state.get(f"d{i}_why", "")}
        for i in range(3)
    ]}
    return need, cases, ideas


# ---------------------------------------------------------------- 挂起重算结果
# 按钮在 tab 里，而 tab 里的控件在本轮已经画过了；
# 直接改 session_state 会被 Streamlit 忽略（"widget already instantiated"）。
# 所以按钮只负责"挂起 + rerun"，真正的赋值在下一次运行的最开头做。
_pending = st.session_state.pop("_pending_regen", None)
if _pending:
    if _pending.get("cases"):
        seed_cases(_pending["cases"])
    if _pending.get("ideas"):
        seed_ideas(_pending["ideas"])


# ---------------------------------------------------------------- 侧边栏
with st.sidebar:
    st.header("输入")
    samples = load_samples()
    pick = st.selectbox("用样例 brief，或选「自定义」", ["自定义"] + list(samples))
    if pick != "自定义" and st.session_state.get("loaded") != pick:
        st.session_state["brief_text"] = samples[pick]
        st.session_state["loaded"] = pick
    if pick == "自定义" and st.session_state.get("loaded") not in (None, "自定义"):
        st.session_state["loaded"] = "自定义"

    st.divider()
    st.subheader("出图")
    used, total = quota_left()
    st.caption(f"wan2.7 免费额度已用 {used}/{total}，剩余 {total - used} 张")
    gen_images = st.checkbox("给前 2 套方向出草图", value=False,
                             help="每张 0.2 元；免费额度内不计费。默认关闭以免误触消费。")

    st.divider()
    st.subheader("导入")
    up = st.file_uploader("导入之前导出的 .json", type=["json"],
                          help="把「导出编辑结果 .json」拖回来，可以接着改、接着出图")
    if up is not None:
        sig = f"{up.name}:{up.size}"
        if st.session_state.get("_import_sig") != sig:
            try:
                data = json.loads(up.getvalue().decode("utf-8"))
                seed_need(data.get("need") or {})
                seed_cases(data.get("cases") or {})
                seed_ideas(data.get("ideas") or {})
                raw_imgs = data.get("imgs") or {}
                st.session_state["imgs"] = {int(k): v for k, v in raw_imgs.items()}
                st.session_state["brief_text"] = data.get("brief", "")
                st.session_state["loaded"] = pick          # 防止样例逻辑把 brief 冲掉
                st.session_state["result"] = {"brief": data.get("brief", "")}
                st.session_state["last_run"] = {"ts": "导入", "dt": 0.0, "cached": True}
                st.session_state["_import_sig"] = sig
                st.success("已导入，可以接着改，也可以直接出图。")
                st.rerun()
            except Exception as e:  # noqa: BLE001
                st.error(f"导入失败：{type(e).__name__}: {e}")

    st.divider()
    st.caption("文本 deepseek-flash ｜ 图像 wan2.7-image ｜ 结果缓存本地")

# ---------------------------------------------------------------- 主区
st.title("Brief → 一页提案")
st.caption("跑完之后，下面所有文字都能直接改；改完提案会跟着变。")

brief = st.text_area(
    "客户 brief（越像微信语音越好，信息缺失是常态）",
    key="brief_text", height=100,
    placeholder="例：那个海报你帮我搞一下，就我们那个新品，要高级一点，周五要",
)

c1, c2 = st.columns([1, 5])
with c1:
    run = st.button("运行", type="primary", width="stretch")
with c2:
    st.caption("同样的 brief 第二次运行会命中本地缓存，不产生新的模型调用、不花钱。")

if run:
    if not brief.strip():
        st.warning("先写一句 brief，或者从左侧选一个样例。")
        st.stop()
    t0 = time.time()
    with st.spinner("跑流水线…"):
        try:
            need0 = P.stage_extract(BID, brief, True)
            cases0 = P.stage_cases(BID, need0, True)
            ideas0 = P.stage_ideas(BID, need0, cases0, True)
            imgs: dict[int, str] = {}
            if gen_images:
                bar = st.progress(0.0, text="出图…")
                for i, d in enumerate(ideas0["directions"][:2]):
                    r = IMG.generate(d["visual_prompt"])
                    imgs[i] = str(r["path"])
                    bar.progress((i + 1) / 2, text=f"{d['name']}：{'缓存' if r['cached'] else '新生成'}")
                bar.empty()
            seed_need(need0)
            seed_cases(cases0)
            seed_ideas(ideas0)
            st.session_state["imgs"] = imgs
            dt = time.time() - t0
            st.session_state["result"] = {"brief": brief}
            st.session_state["last_run"] = {
                "ts": time.strftime("%H:%M:%S"),
                "dt": dt,
                "cached": dt < 1.5,
            }
            st.toast(f"完成 · {dt:.1f} 秒", icon="✅")
        except Exception as e:  # noqa: BLE001
            st.error(f"跑失败了：{type(e).__name__}: {e}")
            st.stop()

if not st.session_state.get("result"):
    st.info("左侧选个样例，或者直接写一句 brief，然后点「运行」。")
    st.stop()

_run = st.session_state.get("last_run") or {}
if _run:
    _tag = "（全部命中缓存，没有调用模型、没有花钱）" if _run.get("cached") else ""
    st.caption(f"本次生成于 {_run.get('ts', '?')}，用时 {_run.get('dt', 0):.1f} 秒{_tag}。"
               "下面改内容不会重新调用模型，只影响提案文字。")

need, cases, ideas = collect()
imgs = st.session_state.get("imgs", {})
md = P.build_proposal_md(BID, need, cases, ideas, imgs).replace("｜ live", "")

tab1, tab2, tab3, tab4 = st.tabs(["① 需求单", "② 参考案例", "③ 三套方向", "④ 一页提案"])

# ---------------- ① 需求单（可编辑）
with tab1:
    st.caption("客户没说的留空即可，留空会在提案里变成「待确认」；客户后来补充了，直接填进来。")
    cols = st.columns(2)
    for i, k in enumerate(P.FIELDS):
        with cols[i % 2]:
            st.text_input(FIELD_CN[k], key=f"need_{k}", placeholder="留空 = 待确认")

    need_now = {k: st.session_state.get(f"need_{k}", "") for k in P.FIELDS}
    miss = [k for k in P.FIELDS if not str(need_now[k]).strip()]
    if miss:
        st.warning(f"还有 {len(miss)} 个字段是空的：{'、'.join(FIELD_CN[k] for k in miss)}")
    else:
        st.success("7 个字段都齐了，提案里不会再有「待确认」。")

    st.divider()
    st.caption(
        "需求单是上游：改了它，下面两页不会自动重算（重算要调模型，还会覆盖你手改过的文字）。"
        "所以每页自己配一个重算按钮。"
    )
    if st.button("① → ② 按当前需求单重新匹配案例"):
        with st.spinner("重新匹配案例…"):
            try:
                need_cur = {k: str(st.session_state.get(f"need_{k}", "")).strip() for k in P.FIELDS}
                need_cur["_missing"] = [k for k in P.FIELDS if not need_cur[k]]
                st.session_state["_pending_regen"] = {"cases": P.stage_cases(BID, need_cur, True)}
                st.session_state["last_run"] = {"ts": time.strftime("%H:%M:%S"), "dt": 0.0,
                                                "cached": True}
                st.rerun()
            except Exception as e:  # noqa: BLE001
                st.error(f"重算失败：{type(e).__name__}: {e}")
    st.caption("会覆盖 ② 页的案例选择与理由；同样的需求单第二次点击命中缓存，不花钱。")

# ---------------- ② 参考案例（可换、理由可改）
with tab2:
    st.caption("可以换成案例库里任意一条；理由也会原样进入提案。")
    cases_db = all_cases()
    ids = list(cases_db)
    for i in range(3):
        with st.container(border=True):
            # 不传 index：默认值由 seed() 写进 session_state，
            # 同时传 index 和 key 会触发 Streamlit 的 "default value + session state" 警告
            cur = st.session_state.get(f"case{i}_id", ids[0])
            if cur not in ids:
                st.session_state[f"case{i}_id"] = ids[0]
            sel = st.selectbox(
                f"案例 {i + 1}", ids,
                key=f"case{i}_id",
                format_func=lambda cid: f"{cid}｜{cases_db[cid]['行业']}｜{cases_db[cid]['客户诉求'][:20]}",
            )
            st.caption(f"最终做法：{cases_db[sel]['最终方案摘要']}")
            st.text_area("为什么选它", key=f"case{i}_reason", height=80)

# ---------------- ③ 三套方向（全可编辑 + 取色器）
with tab3:
    st.caption("文案、画面描述、配色、理由都能改。点色块打开取色器（Chrome 里带吸管）。")
    cols = st.columns(3)
    for i in range(3):
        with cols[i]:
            st.text_input("方向名", key=f"d{i}_name")
            st.text_input("文案", key=f"d{i}_headline")

            cc = st.columns(3)
            for j in range(3):
                with cc[j]:
                    st.color_picker(f"色{j + 1}", key=f"d{i}_c{j}")

            img = imgs.get(i)
            if img:
                st.image(img, width="stretch")

            # 出图按钮常驻：没图时是"生成"，有图时是"重出"。
            # 这样"先看文字、满意再出图"的动线才走得通。
            if st.button("重出这张草图" if img else "生成这张草图", key=f"regen{i}",
                         help="按当前提示词生成，0.2 元/张（免费额度内不计费）"):
                with st.spinner("出图中，约 30 秒…"):
                    try:
                        r = IMG.generate(st.session_state.get(f"d{i}_vp", ""))
                        imgs = dict(st.session_state.get("imgs") or {})
                        imgs[i] = str(r["path"])
                        st.session_state["imgs"] = imgs
                        st.toast(f"{'缓存命中' if r['cached'] else '已生成'} · {r['cost']:.1f} 元",
                                 icon="🖼️")
                        st.rerun()
                    except Exception as e:  # noqa: BLE001
                        st.error(f"出图失败：{e}")

            st.text_area("图像提示词", key=f"d{i}_vp", height=170)
            st.text_area("为什么这套适合", key=f"d{i}_why", height=100)

    st.divider()
    st.caption(
        "这一页的内容来自「需求单 + 参考案例」。改完上游后点下面这个按钮，"
        "三套方向会按当前需求单和当前案例重新生成——**你在本页改过的文案会被覆盖**。"
    )
    if st.button("②③ → 按当前需求单 + 案例重新生成三套方向", type="primary"):
        with st.spinner("按当前需求单和案例重出方向…"):
            try:
                need_cur, cases_cur, _ = collect()
                st.session_state["_pending_regen"] = {
                    "ideas": P.stage_ideas(BID, need_cur, cases_cur, True)
                }
                st.session_state["last_run"] = {"ts": time.strftime("%H:%M:%S"), "dt": 0.0,
                                                "cached": True}
                st.rerun()
            except Exception as e:  # noqa: BLE001
                st.error(f"重算失败：{type(e).__name__}: {e}")
    st.caption("同样的需求单 + 案例第二次点击命中缓存，不花钱。想保留现在的版本，先导出一份 JSON。")

# ---------------- ④ 一页提案（跟着编辑实时变）
with tab4:
    head, tail, third = st.columns([1, 1, 2])
    with head:
        st.download_button("下载 .md", md, file_name="proposal.md", mime="text/markdown",
                           width="stretch")
    with third:
        # 编辑只存在当前会话里，刷新会重置；导出一份 JSON 才能留下
        st.download_button(
            "导出编辑结果 .json",
            json.dumps({"brief": st.session_state.get("result", {}).get("brief", ""),
                        "need": {k: need[k] for k in P.FIELDS},
                        "cases": cases, "ideas": ideas,
                        "imgs": {str(k): v for k, v in imgs.items()}},
                       ensure_ascii=False, indent=2),
            file_name="edited_result.json", mime="application/json", width="stretch",
        )
    st.caption("这一页不是定稿快照——它每次都从上面编辑后的内容重新拼装。编辑只存在当前会话里，刷新会重置，需要留存请下载。")
    st.markdown(md.replace("](img/", f"]({(ROOT / 'data' / 'out' / 'img').as_posix()}/"))

st.divider()
st.caption(
    "个人项目，源于实习期间对图文设计交付流程的观察；公司未立项、无 AI 团队。"
    "素材均为自造脱敏样本，未使用任何客户资料。"
)
