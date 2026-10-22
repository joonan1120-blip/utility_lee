"""Utility 운전일지 현장 입력 + 대시보드 (Streamlit 시연판).

실행:  pip install -r requirements.txt
       streamlit run app.py
같은 와이파이의 휴대폰에서:  http://<PC IP>:8501
"""
from __future__ import annotations

import os
from pathlib import Path

from datetime import date, datetime, time, timedelta

import altair as alt
import pandas as pd
import streamlit as st

import agents as ag
import briefing as bf
import calc
import logbook as lb
import ops
from storage import get_store, make_rows, new_id, now_kst
from users import authenticate, load_users

st.set_page_config(page_title="Utility 운전일지", page_icon="🛠️", layout="centered",
                   initial_sidebar_state="collapsed")

CAMPUS_COLOR = {"1캠퍼스": "#2a78d6", "2캠퍼스": "#eb6834", "3캠퍼스": "#1baf7a"}
st.markdown("""
<style>
  .block-container {padding-top: 1.2rem; padding-bottom: 4rem; max-width: 760px;}
  div[data-testid="stMetricValue"] {font-size: 1.6rem;}
  .prev {color: #6b6a66; font-size: 0.8rem; margin: -0.6rem 0 0.4rem 0;}
  .tag {display:inline-block; padding:1px 8px; border-radius:10px; font-size:0.78rem; margin-left:4px;}
  .tag-bad {background:#fde8e8; color:#a12727;}
  .tag-ok {background:#e6f4e6; color:#1f6b1f;}
  .tag-rec {background:#eceae4; color:#52514e;}
  /* 모바일: 숫자 입력칸 크게 */
  input[type="number"], input[type="text"] {font-size: 1.05rem !important;}
  /* 숫자칸 +/- 버튼 숨김 (모바일 오입력 방지) */
  div[data-testid="stNumberInputContainer"] button {display: none;}
</style>
""", unsafe_allow_html=True)


# ---------------- 데이터 ----------------
@st.cache_resource
def store():
    return get_store(st.secrets)


@st.cache_data(ttl=60, show_spinner=False)
def load(key: str) -> pd.DataFrame:
    return store().read(key)


# 설정 우선순위: .streamlit/secrets.toml(값이 있을 때) → 환경변수 / .env → 기본값
ENV_MAP = {"openai_api_key": "OPENAI_API_KEY", "openai_model": "OPENAI_MODEL",
           "pin": "APP_PIN", "briefing_include_names": "BRIEFING_INCLUDE_NAMES"}


def _load_dotenv():
    p = Path(__file__).parent / ".env"
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()


def secret(name, default):
    try:
        v = st.secrets.get(name, None)
    except Exception:
        v = None
    if v not in (None, ""):
        return v
    ev = os.environ.get(ENV_MAP.get(name, name.upper()))
    return ev if ev not in (None, "") else default


items = load("items")
comps = load("comps")
log = load("log")
maint = load("maint")
panels = load("panels")
loads = load("loads")
items["순서"] = pd.to_numeric(items["순서"], errors="coerce")
items = items.sort_values("순서")
CAMPUSES = list(dict.fromkeys(items["캠퍼스"]))

# ---------------- 로그인 (사번) ----------------
PIN = str(secret("pin", "1234"))
USE_EMP = load_users() is not None      # config/users.csv 가 있으면 사번 로그인, 없으면 공용 PIN
if not st.session_state.get("user"):
    st.title("🛠️ Utility 운전일지")
    with st.form("login"):
        if USE_EMP:
            st.caption("사번으로 로그인하세요.")
            no = st.text_input("사번", max_chars=10, placeholder="예: 2609001")
            pin = st.text_input("PIN (설정된 사번만)", type="password")
        else:
            st.caption("현장 입력용 PIN을 입력하세요." + (" (시연용 기본값 1234)" if PIN == "1234" else ""))
            no, pin = "", st.text_input("PIN", type="password")
        if st.form_submit_button("로그인", type="primary", use_container_width=True):
            if USE_EMP:
                u, err = authenticate(no, pin)
            else:
                u, err = ({"사번": "", "이름": "", "권한": "공용"}, None) if pin == PIN else (None, "PIN이 맞지 않습니다")
            if u:
                st.session_state.user = u
                if u["권한"] != "공용":
                    st.session_state.inspector = u["이름"]
                st.rerun()
            st.error(err)
    st.stop()

USER = st.session_state.user
IS_ADMIN = USER["권한"] == "관리자"
SHARED = USER["권한"] == "공용"


def who(name: str) -> str:
    """기록에 남길 이름. 개인 사번은 사번 이름, 공용 계정은 직접 입력한 이름 + (공용)."""
    if not SHARED:
        return USER["이름"]
    name = (name or "").strip()
    return f"{name}(공용)" if name and USER["사번"] else name


qp = st.query_params  # QR 링크: ?campus=2캠퍼스&equip=공기압축기 1-1
if "campus" not in st.session_state:
    st.session_state.campus = qp.get("campus") if qp.get("campus") in CAMPUSES else CAMPUSES[0]
if "equip" not in st.session_state and qp.get("equip"):
    st.session_state.equip = qp.get("equip")

h1, h2 = st.columns([4, 1], vertical_alignment="center")
h1.title("🛠️ Utility 운전일지")
if h2.button("로그아웃", use_container_width=True):
    for k in list(st.session_state.keys()):
        del st.session_state[k]
    st.rerun()
if USER["사번"]:
    st.caption(f"👤 {USER['이름']} ({USER['사번']}) · {USER['권한']}")
# 탭 대신 메뉴: 선택한 화면만 실행 (브리핑은 메뉴에 들어갈 때만 AI 호출)
MENU = ["📝 입력", "🤖 브리핑", "📊 현황", "⚙️ 압축기", "🔧 정비", "⚡ 부하측정"] + (["🗂 데이터"] if IS_ADMIN or not USE_EMP else [])
if "menu" not in st.session_state:
    st.session_state.menu = MENU[1] if qp.get("view") == "brief" else MENU[0]
page = st.pills("메뉴", MENU, key="menu", selection_mode="single",
                label_visibility="collapsed") or MENU[0]


# ======================= 점검 입력 =======================
def prev_values(campus, equip, d: date) -> dict:
    g = log[(log["캠퍼스"] == campus) & (log["설비명"] == equip) & (log["점검일"] < d.isoformat())]
    g = g.sort_values(["점검일", "점검시각"])
    return {r["점검항목"]: (r["값"], r["점검일"]) for _, r in g.iterrows()}


def num_format(unit: str):
    if unit in ("h", "M2"):
        return 1.0, "%.0f"
    if unit in ("℃", "%", "A", "V", "LPM", "TON", "RPM"):
        return 0.1, "%.1f"
    return 0.01, "%.2f"


if page == "📝 입력":
    campus = st.segmented_control("캠퍼스", CAMPUSES, key="campus", selection_mode="single")
    campus = campus or CAMPUSES[0]
    now = now_kst()
    st.session_state.setdefault("d_in", now.date())
    st.session_state.setdefault("t_in", time(now.hour, (now.minute // 10) * 10))
    c1, c2 = st.columns(2)
    d = c1.date_input("점검일", key="d_in", format="YYYY-MM-DD")
    t = c2.time_input("점검시각", key="t_in", step=600)
    if SHARED:
        inspector = who(st.text_input("점검자", key="inspector", placeholder="이름 (공용 계정은 직접 입력)"))
    else:
        inspector = USER["이름"]
        st.caption(f"점검자: **{inspector}** (사번 로그인)")

    ci = items[items["캠퍼스"] == campus]
    stt = lb.today_status(ci, log, d)
    eqs = (stt.groupby("설비명", sort=False)
           .agg(유형=("설비유형", "first"), 순서=("순서", "min"), 전체=("점검항목", "size"),
                입력=("입력", "sum"), 이탈=("판정", lambda s: int((s == "이탈").sum())))
           .sort_values("순서").reset_index())
    n_done = int((eqs["입력"] == eqs["전체"]).sum())
    st.progress(stt["입력"].mean() if len(stt) else 0.0,
                text=f"{d:%m/%d} 입력 {int(stt['입력'].sum())}/{len(stt)}항목 · 설비 {n_done}/{len(eqs)} 완료"
                     + (f" · ⚠️ 이탈 {int(eqs['이탈'].sum())}건" if eqs["이탈"].sum() else ""))

    types = list(dict.fromkeys(eqs["유형"]))
    ftype = st.pills("설비 유형", types, selection_mode="single", key=f"type_{campus}")
    view = eqs if not ftype else eqs[eqs["유형"] == ftype]

    def label(name):
        r = eqs[eqs["설비명"] == name].iloc[0]
        icon = "⚠️" if r["이탈"] else ("✅" if r["입력"] == r["전체"] else ("🟡" if r["입력"] else "⬜"))
        return f"{icon} {name}  ({int(r['입력'])}/{int(r['전체'])})"

    names = list(view["설비명"])
    if "equip_next" in st.session_state:  # 위젯 생성 전에만 값 변경 가능
        st.session_state.equip = st.session_state.pop("equip_next")
    if st.session_state.get("equip") not in names:
        st.session_state.equip = names[0] if names else None
    cc1, cc2 = st.columns([3, 1], vertical_alignment="bottom")
    equip = cc1.selectbox("설비", names, key="equip", format_func=label)
    if cc2.button("다음 ▶", use_container_width=True, help="다음 미완료 설비로 이동"):
        todo = [n for n in names if eqs.set_index("설비명").loc[n, "입력"] < eqs.set_index("설비명").loc[n, "전체"]]
        after = [n for n in todo if names.index(n) > names.index(equip)] or todo
        if after:
            st.session_state.equip_next = after[0]
            st.rerun()

    flash = st.session_state.pop("flash", None)
    if flash:
        st.success(flash["msg"])
        for w in flash["warns"]:
            st.warning(w)

    if equip:
        ei = ci[ci["설비명"] == equip]
        cur = {r["점검항목"]: r["값"] for _, r in stt[stt["설비명"] == equip].iterrows()
               if isinstance(r["값"], str) and r["값"].strip()}
        prev = prev_values(campus, equip, d)
        crow = comps[(comps["캠퍼스"] == campus) & (comps["설비명"] == equip)]
        with st.container(border=True):
            st.subheader(equip)
            if not crow.empty:
                c = crow.iloc[0]
                st.caption(f"{c['형식']}" + (f" · 운전값 {c['최소운전값']}~{c['최대운전값']} {c['단위']}"
                                            if c["형식"] == "인버터형" else "")
                           + " · 부하/무부하 시간은 계기 누적값")
            with st.form(f"f|{campus}|{equip}|{d}", border=False):
                vals = {}
                for _, it in ei.iterrows():
                    k = it["점검항목"]
                    spec = lb.spec_text(it)
                    saved = cur.get(k)
                    lab = f"**{k}**" + (f"  ·  {spec}" if spec else "")
                    wkey = f"v|{campus}|{equip}|{d}|{k}"
                    if it["입력형태"] == "선택":
                        opts = it["선택지"].split(",")
                        idx = opts.index(saved) if saved in opts else None
                        vals[k] = st.radio(lab, opts, index=idx, horizontal=True, key=wkey)
                    else:
                        step, fmt = num_format(it["단위"])
                        vals[k] = st.number_input(lab, value=lb.num(saved), step=step, format=fmt,
                                                  key=wkey, placeholder=it["단위"] or "값")
                    if k in prev:
                        pv, pd_ = prev[k]
                        st.markdown(f"<div class='prev'>직전 {pv} ({pd_[5:]})</div>", unsafe_allow_html=True)
                memo = st.text_input("메모 (특이사항)", key=f"m|{campus}|{equip}|{d}")
                ok = st.form_submit_button("💾 저장", type="primary", use_container_width=True)
            if ok:
                values = {k: (f"{v:g}" if isinstance(v, float) else v) for k, v in vals.items()
                          if v is not None and str(v) != ""}
                if not inspector.strip():
                    st.error("점검자 이름을 입력하세요")
                elif not values:
                    st.error("입력된 값이 없습니다")
                else:
                    errs, warns, judged = lb.validate_group(ei, values, log, comps, campus, equip,
                                                            d, t.strftime("%H:%M"))
                    if errs:
                        for e in errs:
                            st.error(e)
                    else:
                        store().upsert_log(make_rows(d.isoformat(), t.strftime("%H:%M"), campus, equip,
                                                     inspector.strip(), values, judged, memo))
                        load.clear()
                        # 다음 미완료 설비로 자동 이동
                        done_now = set(eqs.loc[eqs["입력"] == eqs["전체"], "설비명"]) | {equip}
                        nxt = [n for n in names if n not in done_now and names.index(n) > names.index(equip)]
                        if nxt:
                            st.session_state.equip_next = nxt[0]
                        st.session_state.flash = {"msg": f"{equip} 저장 완료 ({len(values)}항목)", "warns": warns}
                        st.rerun()


# ======================= 오늘 현황 =======================
if page == "📊 현황":
    dd = st.date_input("조회일", value=now_kst().date(), format="YYYY-MM-DD", key="dash_d")
    allst = lb.today_status(items, log, dd)
    cols = st.columns(len(CAMPUSES))
    for col, cp in zip(cols, CAMPUSES):
        s = allst[allst["캠퍼스"] == cp]
        bad = int((s["판정"] == "이탈").sum())
        col.metric(cp, f"{s['입력'].mean():.0%}", f"이탈 {bad}건" if bad else "이탈 없음",
                   delta_color="inverse" if bad else "off", help="입력률 = 입력 항목 ÷ 전체 항목")
    dev = allst[allst["판정"] == "이탈"]
    st.markdown("#### ⚠️ SPEC 이탈 항목")
    if dev.empty:
        st.caption("이탈 항목이 없습니다.")
    else:
        dev = dev.assign(SPEC=dev.apply(lb.spec_text, axis=1))
        st.dataframe(dev[["캠퍼스", "설비명", "점검항목", "값", "SPEC", "점검자"]],
                     hide_index=True, use_container_width=True)
    st.markdown("#### ⬜ 미입력 설비")
    miss = (allst.groupby(["캠퍼스", "설비명"], sort=False)["입력"].agg(["sum", "size"]).reset_index())
    miss = miss[miss["sum"] < miss["size"]]
    for cp in CAMPUSES:
        m = miss[miss["캠퍼스"] == cp]
        with st.expander(f"{cp} — {len(m)}대 미완료", expanded=False):
            st.write(", ".join(f"{r['설비명']} ({int(r['sum'])}/{int(r['size'])})" for _, r in m.iterrows()) or "모두 완료")

    st.markdown("#### 📈 항목 추이")
    a1, a2, a3 = st.columns(3)
    tc = a1.selectbox("캠퍼스", CAMPUSES, key="tr_c")
    num_items = items[(items["캠퍼스"] == tc) & (items["입력형태"] == "숫자")]
    te = a2.selectbox("설비", list(dict.fromkeys(num_items["설비명"])), key="tr_e")
    tp = a3.selectbox("항목", list(num_items[num_items["설비명"] == te]["점검항목"]), key="tr_p")
    it = num_items[(num_items["설비명"] == te) & (num_items["점검항목"] == tp)].iloc[0]
    series = log[(log["캠퍼스"] == tc) & (log["설비명"] == te) & (log["점검항목"] == tp)].copy()
    series["날짜"] = pd.to_datetime(series["점검일"], errors="coerce")
    series["값"] = series["값"].map(lb.num)
    series = series[(series["날짜"] >= pd.Timestamp(dd - timedelta(days=60)))].dropna(subset=["값"])
    if series.empty:
        st.caption("최근 60일 기록이 없습니다.")
    else:
        base = alt.Chart(series).encode(x=alt.X("날짜:T", title=None, axis=alt.Axis(format="%m/%d")))
        line = base.mark_line(strokeWidth=2, color=CAMPUS_COLOR.get(tc, "#2a78d6")).encode(
            y=alt.Y("값:Q", title=f"{tp} ({it['단위']})", scale=alt.Scale(zero=False)))
        pts = base.mark_point(size=60, filled=True, color=CAMPUS_COLOR.get(tc, "#2a78d6")).encode(
            y="값:Q", tooltip=[alt.Tooltip("점검일", title="날짜"), "값", "점검자"])
        layers = [line, pts]
        for b in (it["하한"], it["상한"]):
            if b:
                layers.append(alt.Chart(pd.DataFrame({"y": [float(b)]})).mark_rule(
                    strokeDash=[4, 4], color="#d03b3b").encode(y="y:Q"))
        st.altair_chart(alt.layer(*layers).properties(height=240), use_container_width=True)
        st.caption(f"빨간 점선 = 관리 SPEC ({lb.spec_text(it)})")


# ======================= 공기압축기 =======================
def pct(v):
    return "—" if v is None or pd.isna(v) else f"{v:.1%}"


if page == "⚙️ 압축기":
    readings, equipment = lb.compressor_frames(log, comps)
    alld = pd.to_datetime(readings["측정일"], errors="coerce").dropna()
    if alld.empty:
        st.info("공기압축기 부하/무부하 시간 기록이 없습니다.")
    else:
        rng = st.date_input("조회 기간", value=(alld.min().date(), alld.max().date()), format="YYYY-MM-DD")
        s_, e_ = (rng if isinstance(rng, tuple) and len(rng) == 2 else (None, None))
        res = calc.compute(readings, equipment, s_, e_)
        for _, c in res.campus.iterrows():
            if c["입력호기"] == 0:
                continue
            with st.container(border=True):
                st.markdown(f"**{c['캠퍼스']}** · {c['대수']}대"
                            + (f" · {c['시작일']:%m/%d}~{c['종료일']:%m/%d}" if pd.notna(c["시작일"]) else ""))
                m1, m2, m3 = st.columns(3)
                m1.metric("가동률", pct(c["가동률"]), help="기간 부하시간 증가 ÷ 총 가동시간 증가")
                m2.metric("기간 용량 사용률", pct(c["용량사용률"]), help="환산 사용시간 ÷ 보정 경과시간")
                m3.metric("최근 측정 사용률", pct(c["최근사용률"]))
                if c["상태"] != "정상":
                    st.caption(f"⚠️ {c['상태']}")
                if c["보정시간"]:
                    st.caption(f"시간 자동 보정 +{c['보정시간']:.1f}h")
        u = res.units[res.units["건수"] > 0].copy()
        u["표시"] = u["캠퍼스"].str[0] + "캠 " + u["호기"].str.replace("공기압축기 ", "")
        u["가동률(%)"] = u["가동률"] * 100
        u["용량 사용률(%)"] = u["용량사용률"] * 100
        st.markdown("#### 호기별 가동률")
        bar = alt.Chart(u).mark_bar(cornerRadiusEnd=4, size=18).encode(
            y=alt.Y("표시:N", sort=None, title=None),
            x=alt.X("가동률(%):Q", scale=alt.Scale(domain=[0, 100]), title="가동률 (%)"),
            color=alt.Color("캠퍼스:N", scale=alt.Scale(domain=list(CAMPUS_COLOR), range=list(CAMPUS_COLOR.values())),
                            legend=alt.Legend(orient="top", title=None)),
            tooltip=["캠퍼스", "호기", "형식", alt.Tooltip("가동률(%):Q", format=".1f"),
                     alt.Tooltip("용량 사용률(%):Q", format=".1f"), "상태"])
        st.altair_chart(bar.properties(height=max(160, 26 * len(u))), use_container_width=True)

        st.markdown("#### 일별 용량 사용률 추이")
        tr = calc.daily_trend(res.segs)
        if not tr.empty:
            tr["날짜"] = pd.to_datetime(tr["날짜"])
            tr["용량 사용률(%)"] = tr["용량사용률"] * 100
            lc = alt.Chart(tr).encode(
                x=alt.X("날짜:T", title=None, axis=alt.Axis(format="%m/%d")),
                y=alt.Y("용량 사용률(%):Q", scale=alt.Scale(domain=[0, 100])),
                color=alt.Color("캠퍼스:N", scale=alt.Scale(domain=list(CAMPUS_COLOR), range=list(CAMPUS_COLOR.values())),
                                legend=alt.Legend(orient="top", title=None)))
            st.altair_chart((lc.mark_line(strokeWidth=2) + lc.mark_point(size=60, filled=True).encode(
                tooltip=[alt.Tooltip("날짜:T", format="%Y-%m-%d"), "캠퍼스",
                         alt.Tooltip("용량 사용률(%):Q", format=".1f")])).properties(height=240),
                use_container_width=True)
        with st.expander("호기별 상세표"):
            show = u[["캠퍼스", "호기", "형식", "기간총", "기간부하", "가동률", "용량사용률", "최근로드율", "상태"]].copy()
            for col in ("가동률", "용량사용률", "최근로드율"):
                show[col] = show[col].map(pct)
            st.dataframe(show, hide_index=True, use_container_width=True)


# ======================= 데이터 =======================
if page == "🗂 데이터" and (IS_ADMIN or not USE_EMP):
    st.caption(f"저장소: {store().name}")
    st.download_button("점검기록 CSV 내려받기", log.to_csv(index=False).encode("utf-8-sig"),
                       file_name=f"점검기록_{now_kst():%Y%m%d}.csv", mime="text/csv",
                       use_container_width=True)
    if st.button("새로고침 (시트 다시 읽기)", use_container_width=True):
        load.clear()
        st.rerun()
    st.markdown("#### 최근 기록")
    st.dataframe(log.sort_values("저장시각", ascending=False).head(50), hide_index=True,
                 use_container_width=True)
    with st.expander(f"점검항목 마스터 ({len(items)}개)"):
        st.dataframe(items, hide_index=True, use_container_width=True)
    with st.expander("압축기 설정"):
        st.dataframe(comps, hide_index=True, use_container_width=True)


# ======================= 🔧 정비일지 =======================
if page == "🔧 정비":
    today = now_kst().date()
    ms = ops.maint_status(maint, today)
    due = ms[ms["예정상태"].isin(["지연", "임박"])]
    if len(due):
        st.warning("정비 지연·임박: " + ", ".join(
            f"{r['캠퍼스'][0]}캠 {r['설비']} {r['정비내용']} ({r['다음예정일']}, {r['예정상태']})" for _, r in due.iterrows()))
    with st.expander("➕ 정비 기록 추가", expanded=not len(maint)):
        with st.form("maint_add", clear_on_submit=True):
            c1, c2 = st.columns(2)
            m_camp = c1.selectbox("캠퍼스", CAMPUSES, key="m_camp")
            m_cat = c2.selectbox("분류", ops.MAINT_CATS, key="m_cat")
            known = sorted(set(maint["설비"]) | set(items["설비명"]))
            m_eq = st.text_input("설비", placeholder="예: 항온항습기 2-1, 진공펌프 1호기, 2F-5번 판넬")
            st.caption("기존 설비명: " + ", ".join(known[:12]) + " …")
            m_work = st.text_input("정비내용", placeholder="예: 흡입 필터, OA필터 교체")
            c3, c4 = st.columns(2)
            m_date = c3.date_input("정비일", value=today, format="YYYY-MM-DD")
            m_cyc = c4.number_input("주기(일) — 주기 정비만", min_value=0, value=0, step=1)
            m_note = st.text_input("비고", placeholder="후속 조치, 특이사항")
            m_who = who(st.text_input("등록자", value=st.session_state.get("inspector", ""), disabled=not SHARED))
            if st.form_submit_button("💾 저장", type="primary", use_container_width=True):
                if not (m_eq.strip() and m_work.strip()):
                    st.error("설비와 정비내용을 입력하세요")
                else:
                    store().upsert("maint", [dict(
                        기록ID=new_id("M"), 등록시각=now_kst().strftime("%Y-%m-%d %H:%M:%S"), 정비일=m_date.isoformat(),
                        캠퍼스=m_camp, 분류=m_cat, 설비=m_eq.strip(), 정비내용=m_work.strip(), 비고=m_note.strip(),
                        주기일=str(int(m_cyc)) if m_cyc else "",
                        다음예정일=(m_date + timedelta(days=int(m_cyc))).isoformat() if m_cyc else "",
                        등록자=m_who.strip())])
                    load.clear()
                    st.success("저장했습니다")
                    st.rerun()
    f1, f2 = st.columns(2)
    fc = f1.selectbox("분류", ["전체"] + ops.MAINT_CATS, key="mf_cat")
    fk = f2.text_input("검색", placeholder="설비·내용", key="mf_q")
    v = ms.copy()
    if fc != "전체":
        v = v[v["분류"] == fc]
    if fk:
        v = v[v["설비"].str.contains(fk, regex=False) | v["정비내용"].str.contains(fk, regex=False)]
    st.dataframe(v.sort_values("정비일", ascending=False)[
        ["정비일", "캠퍼스", "분류", "설비", "정비내용", "비고", "다음예정일", "예정상태", "등록자"]],
        hide_index=True, use_container_width=True)
    st.caption("🤖 브리핑 챗봇에 \"오늘 3캠 진공펌프 1호기 슬러지 클리닝 했어\"처럼 말해도 등록 제안을 받을 수 있습니다.")


# ======================= ⚡ 수변전 월간 부하측정 =======================
if page == "⚡ 부하측정":
    now = now_kst()
    ms_ = ops.months(loads)
    opts = sorted(set(ms_) | {now.strftime("%Y-%m")}, reverse=True)
    c1, c2 = st.columns(2)
    month = c1.selectbox("측정월", opts, key="ld_m")
    lcamp = c2.selectbox("캠퍼스", sorted(set(panels["캠퍼스"])), key="ld_c")
    t = ops.load_table(panels, loads, month)
    t = t[t["캠퍼스"] == lcamp]
    done = (t["상태"] != "").sum()
    st.progress(done / len(t) if len(t) else 0.0,
                text=f"{month} {lcamp} 측정 {done}/{len(t)} 판넬" + (f" · 직전 {t['직전월'].iloc[0]}" if len(t) and t["직전월"].iloc[0] else ""))
    hi = t[t["부하율"].fillna(0) >= 0.8]
    if len(hi):
        st.error("부하율 80% 이상: " + ", ".join(f"{r['패널']} {r['부하율'] * 100:.0f}%" for _, r in hi.iterrows()))
    floors = sorted(set(t["층"]))
    fl = st.pills("층", floors, key=f"ld_f_{lcamp}", selection_mode="single") or (floors[0] if floors else None)
    tf = t[t["층"] == fl]
    with st.form(f"ld|{month}|{lcamp}|{fl}", border=False):
        ld_date = st.date_input("측정일", value=now.date(), format="YYYY-MM-DD")
        ld_who = who(st.text_input("측정자", value=st.session_state.get("inspector", ""), disabled=not SHARED))
        vals = {}
        for _, r in tf.iterrows():
            with st.container(border=True):
                head = f"**{r['패널']}** · {r['전압']} · 차단기 {r['차단기A'] or '?'}A"
                if r["비고"]:
                    head += f" · {r['비고']}"
                st.markdown(head)
                a, b = st.columns([2, 1])
                vals[r["패널ID"]] = (
                    a.number_input("실부하 [A]", value=lb.num(r["실부하A"]), step=0.1, format="%.1f",
                                   key=f"ldv|{month}|{r['패널ID']}", placeholder="A"),
                    b.selectbox("상태", ops.LOAD_STATES,
                                index=ops.LOAD_STATES.index(r["상태"]) if r["상태"] in ops.LOAD_STATES else 0,
                                key=f"lds|{month}|{r['패널ID']}"))
                prev = f"직전 {r['직전실부하A']}A" if r["직전실부하A"] else "직전 기록 없음"
                rate = f" · 현재 부하율 {r['부하율'] * 100:.0f}%" if r["부하율"] is not None and not pd.isna(r["부하율"]) else ""
                st.caption(prev + rate)
        if st.form_submit_button(f"💾 {fl} 저장", type="primary", use_container_width=True):
            rows = []
            ts = now_kst().strftime("%Y-%m-%d %H:%M:%S")
            for pid, (v, stt) in vals.items():
                if v is None and stt == "측정":
                    continue
                r = tf[tf["패널ID"] == pid].iloc[0]
                rows.append(dict(기록ID=new_id("L"), 등록시각=ts, 측정월=month, 측정일=ld_date.isoformat(),
                                 캠퍼스=r["캠퍼스"], 패널ID=pid, 패널=r["패널"], 전압=r["전압"],
                                 실부하A="" if v is None or stt != "측정" else f"{v:g}", 상태=stt,
                                 측정자=ld_who.strip(), 메모=""))
            if rows:
                store().upsert("loads", rows)
                load.clear()
                st.success(f"{len(rows)}개 판넬 저장")
                st.rerun()
            else:
                st.error("입력된 값이 없습니다")
    with st.expander("판넬 전체 표"):
        show = t[["층", "패널", "전압", "차단기A", "실부하A", "상태", "부하율", "직전실부하A", "증감A", "비고"]].copy()
        show["부하율"] = show["부하율"].map(lambda x: "" if x is None or pd.isna(x) else f"{x * 100:.0f}%")
        st.dataframe(show, hide_index=True, use_container_width=True)


# ======================= 🤖 브리핑 (총괄 + 담당자 4명) =======================
def md_lines(text: str) -> str:
    """마크다운에서 한 줄 바꿈이 무시되지 않도록 줄 끝에 강제 줄바꿈(공백 2칸) 추가."""
    return "\n".join(l if (not l.strip() or l.endswith("  ")) else l + "  " for l in str(text).split("\n"))


def md_stream(gen):
    for chunk in gen:
        yield str(chunk).replace("\n", "  \n")


def _propose(rec):
    st.session_state.setdefault("pending_maint", []).append(rec)


if page == "🤖 브리핑":
    api_key = str(secret("openai_api_key", "") or "")
    model = str(secret("openai_model", "gpt-6.1-sol"))
    include_names = str(secret("briefing_include_names", "false")).lower() == "true"
    D = dict(items=items, comps=comps, log=log, maint=maint, panels=panels, loads=loads)
    b1, b2 = st.columns([3, 1], vertical_alignment="center")
    b1.caption(f"총괄 + 담당 4명(전기·항온항습기·공기압축기·기타) · 이번 주 기준 · {model}" if api_key
               else "API 키 없음 → 규칙 기반 요약")
    if b2.button("🔄 새 브리핑", use_container_width=True):
        for k in ("chat", "pending_maint"):
            st.session_state.pop(k, None)
        load.clear()
        st.rerun()

    if not api_key:
        with st.chat_message("assistant"):
            st.markdown(ag.fallback(D, now_kst()))
        st.info("AI 대화는 `.streamlit/secrets.toml`에 `openai_api_key`를 넣으면 켜집니다.")
    else:
        if "chat" not in st.session_state:
            now = now_kst()
            ctxs = {n: ag.domain_context(n, D, now, include_names) for n in ag.NAMES}
            with st.status("담당자 4명에게 이번 주 보고를 받는 중…", expanded=True) as s:
                reports = ag.collect_reports(api_key, model, ctxs,
                                             on_done=lambda n, r: s.write(f"✔ {n} 담당 보고 완료 — 이슈 {len(r.get('이슈', []))}건"))
                s.update(label="담당자 보고 완료 → 총괄 정리", state="complete", expanded=False)
            st.session_state.chat = {"now": now, "ctxs": ctxs, "reports": reports,
                                     "system": ag.master_system(reports, ag.overview(D, now, include_names)),
                                     "msgs": [], "pending": True}
        ch = st.session_state.chat
        for m in ch["msgs"]:
            if not m.get("hidden"):
                with st.chat_message(m["role"]):
                    st.markdown(md_lines(m["content"]))
        if ch["pending"]:
            ch["msgs"].append({"role": "user", "content": "브리핑 시작", "hidden": True})
            with st.chat_message("assistant"):
                try:
                    text = st.write_stream(md_stream(bf.stream_openai(api_key, model, ch["system"],
                                                            [{"role": "user", "content": "브리핑 시작"}])))
                    text = text if isinstance(text, str) else "".join(map(str, text))
                except Exception as e:
                    text = f"⚠️ 총괄 호출 실패: {e}\n\n" + ag.fallback(D, ch["now"])
                    st.markdown(text)
            ch["msgs"].append({"role": "assistant", "content": text})
            ch["pending"] = False

        # 정비일지 등록 제안 → 사용자 확인
        for i, rec in enumerate(list(st.session_state.get("pending_maint", []))):
            with st.container(border=True):
                st.markdown(f"**🔧 정비일지 등록 제안** — {rec['캠퍼스']} · {rec['분류']} · **{rec['설비']}**  \n"
                            f"{rec['정비일']} · {rec['정비내용']}" + (f" · 비고: {rec['비고']}" if rec["비고"] else "")
                            + (f" · 주기 {rec['주기일']}일 → 다음 {rec['다음예정일']}" if rec["주기일"] else ""))
                k1, k2 = st.columns(2)
                if k1.button("💾 저장", key=f"pm_ok{i}", type="primary", use_container_width=True):
                    store().upsert("maint", [dict(rec, 기록ID=new_id("M"), 등록시각=now_kst().strftime("%Y-%m-%d %H:%M:%S"),
                                                  등록자=f"{who(st.session_state.get('inspector', '')) or '챗봇'}(챗봇)")])
                    st.session_state.pending_maint.pop(i)
                    ch["msgs"].append({"role": "assistant", "content": f"✅ 정비일지 저장: {rec['설비']} · {rec['정비내용']} ({rec['정비일']})"})
                    load.clear()
                    st.rerun()
                if k2.button("취소", key=f"pm_no{i}", use_container_width=True):
                    st.session_state.pending_maint.pop(i)
                    ch["msgs"].append({"role": "assistant", "content": f"정비일지 등록 취소: {rec['설비']}"})
                    st.rerun()

        q = st.chat_input("질문 또는 정비 내용 입력 (예: 3캠 진공펌프 1호기 슬러지 클리닝 완료)")
        if q:
            ch["msgs"].append({"role": "user", "content": q})
            with st.chat_message("user"):
                st.markdown(q)
            with st.chat_message("assistant"):
                with st.status("총괄 검토 중…", expanded=False) as s:
                    impl = ag.tool_impl(api_key, model, D, ch["ctxs"], ch["reports"], ch["now"], _propose)
                    label = {"ask_specialist": "담당자 확인", "search_maintenance": "정비일지 검색",
                             "get_item_trend": "추이 조회", "propose_maintenance_record": "정비일지 등록 제안"}
                    try:
                        text = ag.chat_turn(api_key, model, ch["system"], ch["msgs"], impl,
                                            on_tool=lambda n, a: s.write(f"· {label.get(n, n)}: {a[:80]}"))
                    except Exception as e:
                        text = f"⚠️ 응답 실패: {e}"
                    s.update(label="완료", state="complete")
                st.markdown(md_lines(text))
            ch["msgs"].append({"role": "assistant", "content": text})
            if st.session_state.get("pending_maint"):
                st.rerun()

    if "chat" in st.session_state:
        with st.expander("담당자별 보고서"):
            for n, r in st.session_state.chat["reports"].items():
                st.markdown(f"**{n}** — {r.get('요약', '')}")
                for x in r.get("이슈", []):
                    st.markdown(f"- [{x['등급']}] {x['캠퍼스']} {x['설비']} · {x['항목']} — {x['근거']} → {x['권고']}")
                for x in r.get("정비예정", []):
                    st.markdown(f"- 🔧 {x['설비']} {x['내용']} ({x['예정일']}, {x['상태']})")
