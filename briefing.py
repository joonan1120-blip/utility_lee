"""주간 유틸리티 브리핑: 누적 점검기록 → '이번 주' 컨텍스트(JSON) → OpenAI 챗봇.

- 기간: 이번 주 = 월요일 0시 ~ 호출 시점 (한국 시간), 지난주는 비교용
- 수치·판정·상태분류는 코드가 계산하고, AI는 설명·우선순위 판단만 담당
- API 키가 없으면 규칙 기반 요약(fallback_report)만 표시
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

import calc
import logbook as lb

WD = "월화수목금토일"
PROMPT_FILE = Path(__file__).parent / "prompts" / "briefing_system.md"
SKIP_JUMP = ("부하 시간", "무부하 시간", "방류조 유량계")   # 누적값 항목은 급변 판정 제외


def week_range(today: date):
    ws = today - timedelta(days=today.weekday())        # 월요일 시작
    return ws, today, ws - timedelta(days=7), ws - timedelta(days=1)


def _md(d: str) -> str:
    return d[5:] if isinstance(d, str) and len(d) >= 10 else d


def _urgent_type(it) -> bool:
    t, p = it["설비유형"], it["점검항목"]
    if t == "폐수처리":
        return True
    if t == "실내 환경" or (t == "항온항습기" and p in ("온도", "습도")):
        return True
    if t == "공기압축기" and p in ("토출온도", "토출 압력"):
        return True
    if t == "진공설비" and p == "TANK 압력":
        return True
    if t == "CDU(순수)" and p == "비저항":
        return True
    return False


def _near_bound(it, v) -> bool:
    lo, hi = lb.num(it["하한"]), lb.num(it["상한"])
    if v is None or (lo is None and hi is None):
        return False
    if lo is not None and hi is not None:
        m = (hi - lo) * 0.1
        return (lo <= v <= lo + m) or (hi - m <= v <= hi)
    b = lo if lo is not None else hi
    m = abs(b) * 0.1 or 0.1
    return (lo is not None and lo <= v <= lo + m) or (hi is not None and hi - m <= v <= hi)


def _compressor_period(readings, equipment, start: date, end: date):
    """기간 집계: 기간 직전 마지막 측정 → 기간 내 마지막 측정 (주 경계를 넘는 구간 포함)."""
    d = pd.to_datetime(readings["측정일"], errors="coerce").dt.date
    before = d[d < start]
    s = before.max() if len(before) else start
    return calc.compute(readings, equipment, s, end)


def build_weekly_context(items: pd.DataFrame, log: pd.DataFrame, comps: pd.DataFrame,
                         now: datetime, include_names: bool = False) -> dict:
    today = now.date()
    ws, we, lws, lwe = week_range(today)
    S = lambda x: x.isoformat()
    lg = log.copy()
    wk = lg[(lg["점검일"] >= S(ws)) & (lg["점검일"] <= S(we))]
    lw = lg[(lg["점검일"] >= S(lws)) & (lg["점검일"] <= S(lwe))]
    key = ["캠퍼스", "설비명", "점검항목"]
    it_idx = items.set_index(key)

    # --- 입력 현황 (평일 + 기록이 있는 주말, 오늘 포함) ---
    days = [ws + timedelta(days=i) for i in range((we - ws).days + 1)]
    entry = []
    for cp in dict.fromkeys(items["캠퍼스"]):
        ci = items[items["캠퍼스"] == cp]
        per_day, miss, rates = [], [], []
        for d in days:
            day = wk[(wk["캠퍼스"] == cp) & (wk["점검일"] == S(d))]
            if d.weekday() >= 5 and day.empty and d != today:
                continue
            st_ = lb.today_status(ci, wk, d)
            r = float(st_["입력"].mean()) if len(st_) else 0.0
            rates.append(r)
            per_day.append({"날짜": f"{_md(S(d))}({WD[d.weekday()]})", "입력률": round(r, 3)})
            g = st_.groupby("설비명", sort=False)["입력"].agg(["sum", "size"])
            inc = list(g[g["sum"] < g["size"]].index)
            if inc:
                miss.append({"날짜": _md(S(d)), "미완료설비수": len(inc), "설비": inc[:8]})
        entry.append({"캠퍼스": cp, "이번주_입력률": round(sum(rates) / len(rates), 3) if rates else 0,
                      "일자별": per_day, "미입력": miss})

    # --- 이탈 / 경계근접 / 급변 ---
    devs, near, jumps = [], [], []
    for k, g in wk.groupby(key, sort=False):
        if k not in it_idx.index:
            continue
        it = it_idx.loc[k]
        it = (it.iloc[0] if isinstance(it, pd.DataFrame) else it).to_dict()
        it.update(dict(zip(key, k)))
        g = g.sort_values(["점검일", "점검시각"])
        recs = [{"날짜": _md(r["점검일"]), "값": r["값"], "판정": r["판정"] or lb.judge(it, r["값"])}
                for _, r in g.iterrows()]
        latest = recs[-1]
        n_bad = sum(1 for r in recs if r["판정"] == "이탈")
        base = {"캠퍼스": k[0], "설비": k[1], "항목": k[2], "단위": it["단위"], "SPEC": lb.spec_text(it)}
        if n_bad:
            if n_bad >= 2:
                cls = "반복"
            elif latest["판정"] == "이탈":
                cls = "신규" if latest["날짜"] == _md(S(today)) else "지속"
            else:
                cls = "복귀"
            prev_vals = [lb.num(v) for v in lw[(lw["캠퍼스"] == k[0]) & (lw["설비명"] == k[1]) &
                                               (lw["점검항목"] == k[2])]["값"]]
            prev_vals = [v for v in prev_vals if v is not None]
            devs.append(dict(base, 이번주_기록=recs, 최신값=latest, 이탈일수=n_bad, 상태분류=cls,
                             지난주_평균=round(sum(prev_vals) / len(prev_vals), 2) if prev_vals else None,
                             긴급대상=_urgent_type(it)))
        elif it["입력형태"] == "숫자":
            v = lb.num(latest["값"])
            if latest["판정"] == "정상" and _near_bound(it, v):
                near.append(dict(base, 최신값=latest["값"], 날짜=latest["날짜"]))
            if k[2] not in SKIP_JUMP and len(recs) >= 2:
                a, b = lb.num(recs[-2]["값"]), v
                if a is not None and b is not None and abs(b - a) > max(abs(a) * 0.3, 1e-9) and abs(b - a) >= 1:
                    jumps.append(dict(base, 이전=f"{recs[-2]['값']} ({recs[-2]['날짜']})",
                                      최신=f"{latest['값']} ({latest['날짜']})"))

    # --- 메모 ---
    memos = []
    m = wk[wk["메모"].fillna("").str.strip() != ""].drop_duplicates(["점검일", "캠퍼스", "설비명", "메모"])
    for _, r in m.sort_values("점검일").iterrows():
        x = {"날짜": _md(r["점검일"]), "캠퍼스": r["캠퍼스"], "설비": r["설비명"], "내용": r["메모"]}
        if include_names:
            x["점검자"] = r["점검자"]
        memos.append(x)

    # --- 공기압축기 이번 주 vs 지난주 ---
    readings, equipment = lb.compressor_frames(lg, comps)
    comp = []
    if len(readings):
        cur = _compressor_period(readings, equipment, ws, we)
        prv = _compressor_period(readings, equipment, lws, lwe)
        pc = prv.campus.set_index("캠퍼스")
        R = lambda v: None if v is None or pd.isna(v) else round(float(v), 3)
        for _, c in cur.campus.iterrows():
            if c["입력호기"] == 0:
                continue
            p = pc.loc[c["캠퍼스"]] if c["캠퍼스"] in pc.index else None
            units = cur.units[cur.units["캠퍼스"] == c["캠퍼스"]]
            comp.append({
                "캠퍼스": c["캠퍼스"], "대수": int(c["대수"]),
                "이번주": {"가동률": R(c["가동률"]), "용량사용률": R(c["용량사용률"]),
                         "구간": f"{_md(S(c['시작일']))}~{_md(S(c['종료일']))}" if pd.notna(c["시작일"]) else None},
                "지난주": {"가동률": R(p["가동률"]) if p is not None else None,
                         "용량사용률": R(p["용량사용률"]) if p is not None else None},
                "최근사용률": R(c["최근사용률"]), "상태": c["상태"], "보정시간": R(c["보정시간"]),
                "호기별": [{"호기": u["호기"], "가동률": R(u["가동률"]), "용량사용률": R(u["용량사용률"]),
                          "상태": u["상태"]} for _, u in units.iterrows()],
            })

    n_days = wk["점검일"].nunique()
    return {
        "기준시각": now.strftime("%Y-%m-%d %H:%M"),
        "이번주": {"시작": f"{S(ws)}({WD[ws.weekday()]})", "종료": f"{S(we)}({WD[we.weekday()]})", "점검일수": int(n_days)},
        "지난주": {"시작": S(lws), "종료": S(lwe)},
        "입력현황": entry,
        "이탈": sorted(devs, key=lambda x: (not x["긴급대상"], -x["이탈일수"])),
        "경계근접": near[:15], "급변": jumps[:10], "메모": memos[-30:],
        "공기압축기": comp,
        "공기압축기_기준": "가동률 = 부하 증가 ÷ 총(부하+무부하) 증가. 기간 = 기간 직전 마지막 측정 → 기간 내 마지막 측정",
    }


def system_prompt(ctx: dict) -> str:
    return PROMPT_FILE.read_text(encoding="utf-8").replace(
        "{context_json}", json.dumps(ctx, ensure_ascii=False, indent=1))


# ---------------- API 없이 보여줄 규칙 기반 요약 ----------------
def _pct(v):
    return "—" if v is None else f"{v * 100:.1f}%"


def fallback_report(ctx: dict) -> str:
    urg, cau, obs = [], [], []
    for d in ctx["이탈"]:
        line = (f"{d['캠퍼스'][0]}캠 {d['설비']} · {d['항목']} — {d['최신값']['값']}{d['단위']} "
                f"({d['최신값']['날짜']}, SPEC {d['SPEC']}) · {d['상태분류']} {d['이탈일수']}회")
        if d["긴급대상"] and (d["상태분류"] in ("지속", "신규", "반복")):
            urg.append(line)
        elif d["상태분류"] == "복귀":
            obs.append(line)
        else:
            cau.append(line)
    for n in ctx["경계근접"]:
        obs.append(f"{n['캠퍼스'][0]}캠 {n['설비']} · {n['항목']} {n['최신값']}{n['단위']} — SPEC({n['SPEC']}) 경계 근접")
    for c in ctx["공기압축기"]:
        if c["상태"] != "정상":
            cau.append(f"{c['캠퍼스']} 공기압축기 — {c['상태']}")
    rate = [e["이번주_입력률"] for e in ctx["입력현황"]]
    w = ctx["이번주"]
    out = [f"**[주간 유틸리티 브리핑] {w['시작'][5:]}~{w['종료'][5:]} · {ctx['기준시각'][11:]} 기준**",
           f"요약: 긴급 {len(urg)}건 · 주의 {len(cau)}건 · 관찰 {len(obs)}건, "
           f"이번 주 입력률 {_pct(sum(rate) / len(rate) if rate else 0)} (데이터 {w['점검일수']}일치)", ""]
    for title, arr, lim in (("🔴 긴급", urg, 10), ("🟠 주의", cau, 10), ("🟡 관찰", obs, 3)):
        out.append(f"**{title}**" + ("" if arr else " 없음"))
        out += [f"- {x}" for x in arr[:lim]]
        if len(arr) > lim:
            out.append(f"- 외 {len(arr) - lim}건")
    if ctx["공기압축기"]:
        out.append("\n**공기압축기 (이번 주 / 지난주)**")
        for c in ctx["공기압축기"]:
            t, p = c["이번주"], c["지난주"]
            out.append(f"- {c['캠퍼스']}: 가동률 {_pct(t['가동률'])} / {_pct(p['가동률'])}, "
                       f"용량 사용률 {_pct(t['용량사용률'])} / {_pct(p['용량사용률'])}")
    out.append("\n**입력 현황**")
    for e in ctx["입력현황"]:
        miss = sum(m["미완료설비수"] for m in e["미입력"])
        out.append(f"- {e['캠퍼스']}: {_pct(e['이번주_입력률'])}" + (f" · 미완료 {miss}건(설비·일)" if miss else ""))
    if ctx["메모"]:
        out.append("\n**이번 주 메모**")
        out += [f"- {m['날짜']} {m['캠퍼스'][0]}캠 {m['설비']}: {m['내용']}" for m in ctx["메모"][-5:]]
    return "\n".join(out)


# ---------------- OpenAI ----------------
def stream_openai(api_key: str, model: str, system: str, messages: list[dict]):
    """Responses API 스트리밍. 텍스트 조각을 yield."""
    from openai import OpenAI
    client = OpenAI(api_key=api_key)
    stream = client.responses.create(model=model, instructions=system, input=messages, stream=True)
    got = False
    for ev in stream:
        t = getattr(ev, "type", "")
        if t == "response.output_text.delta":
            got = True
            yield ev.delta
        elif t == "response.completed" and not got:
            txt = getattr(ev.response, "output_text", "")
            if txt:
                yield txt
        elif t in ("error", "response.failed"):
            err = getattr(ev, "error", None) or getattr(getattr(ev, "response", None), "error", None)
            raise RuntimeError(getattr(err, "message", str(err)))
