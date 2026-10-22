"""정비일지 · 수변전 월간 부하측정 공통 로직."""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

import logbook as lb

MAINT_CATS = ["항온항습기", "공기압축기", "DI 발생기", "진공펌프", "에어샤워", "전기/수변전", "기타"]
LOAD_STATES = ["측정", "측정불가", "전원 OFF", "미확인"]


# ---------------- 정비 ----------------
def maint_status(maint: pd.DataFrame, today: date) -> pd.DataFrame:
    """다음예정일 기준 상태: 지연 / 임박(7일 이내) / 예정 / ''."""
    m = maint.copy()
    if m.empty:
        m["예정상태"] = []
        return m
    nd = pd.to_datetime(m["다음예정일"], errors="coerce").dt.date
    # 같은 설비·같은 정비내용이 이후에 다시 기록됐으면 이전 예정은 완료로 본다
    m["_d"] = pd.to_datetime(m["정비일"], errors="coerce").dt.date
    latest = m.groupby(["캠퍼스", "설비", "정비내용"])["_d"].transform("max")
    done = m["_d"] < latest

    def st(n, dn):
        if dn or pd.isna(n):
            return ""
        if n < today:
            return "지연"
        if n <= today + timedelta(days=7):
            return "임박"
        return "예정"
    m["예정상태"] = [st(n, dn) for n, dn in zip(nd, done)]
    return m.drop(columns="_d")


def maint_for(maint: pd.DataFrame, cats: list[str], today: date, days: int = 90) -> dict:
    m = maint_status(maint[maint["분류"].isin(cats)], today)
    since = (today - timedelta(days=days)).isoformat()
    recent = m[m["정비일"] >= since].sort_values("정비일")
    due = m[m["예정상태"].isin(["지연", "임박"])]
    cols = ["정비일", "캠퍼스", "설비", "정비내용", "비고", "다음예정일"]
    return {
        f"최근{days}일_정비이력": recent[cols].to_dict("records")[-40:],
        "정비예정_지연임박": due[cols + ["예정상태"]].to_dict("records"),
    }


# ---------------- 부하측정 ----------------
def months(loads: pd.DataFrame) -> list[str]:
    return sorted(set(loads["측정월"]) - {""}) if not loads.empty else []


def load_table(panels: pd.DataFrame, loads: pd.DataFrame, month: str | None = None) -> pd.DataFrame:
    """패널별 지정 월(없으면 최신 월)과 직전 측정월 비교, 부하율 계산."""
    ms = months(loads)
    month = month or (ms[-1] if ms else "")
    prev = max([m for m in ms if m < month], default="")
    t = panels.copy()
    for tag, mm in (("", month), ("직전", prev)):
        l = loads[loads["측정월"] == mm][["패널ID", "실부하A", "상태", "측정일"]]
        l = l.rename(columns={c: f"{tag}{c}" for c in l.columns if c != "패널ID"})
        t = t.merge(l, on="패널ID", how="left")
    t = t.fillna("")
    a, b, p = t["실부하A"].map(lb.num), t["차단기A"].map(lb.num), t["직전실부하A"].map(lb.num)
    t["부하율"] = [round(x / y, 3) if (x is not None and y) else None for x, y in zip(a, b)]
    t["증감A"] = [round(x - y, 1) if (x is not None and y is not None) else None for x, y in zip(a, p)]
    t["측정월"], t["직전월"] = month, prev
    return t


def electric_context(panels: pd.DataFrame, loads: pd.DataFrame, today: date) -> dict:
    t = load_table(panels, loads)
    if t.empty:
        return {"부하측정": "데이터 없음"}
    month, prev = t["측정월"].iloc[0], t["직전월"].iloc[0]
    keep = ["캠퍼스", "층", "패널", "전압", "차단기A", "실부하A", "부하율", "직전실부하A", "증감A", "비고", "상위"]
    rec = lambda df: df[keep].to_dict("records")
    measured = t[t["상태"] == "측정"]
    high = measured[measured["부하율"].fillna(0) >= 0.8].sort_values("부하율", ascending=False)
    jump = measured[[(d is not None and pa not in ("", None) and lb.num(pa) and d >= 10 and d / lb.num(pa) >= 0.3)
                     for d, pa in zip(measured["증감A"], measured["직전실부하A"])]]
    top = measured.sort_values("부하율", ascending=False, na_position="last").head(10)
    nobreaker = measured[measured["부하율"].isna()]
    un = t[t["상태"] != "측정"]
    this_month = today.strftime("%Y-%m")
    return {
        "측정주기": "월 1회 수변전 부하측정",
        "최신측정월": month, "직전측정월": prev or None,
        "이번달_측정여부": month == this_month,
        "패널수": int(len(t)), "측정완료": int(len(measured)),
        "부하율기준": "부하율 = 실부하[A] ÷ 패널 차단기 정격[A]",
        "부하율_80이상": rec(high),
        "전월대비_급증": rec(jump),
        "부하율_상위10": rec(top),
        "차단기정격_없음": [f"{r['캠퍼스'][0]}캠 {r['패널']}({r['전압']})" for _, r in nobreaker.iterrows()],
        "미측정_측정불가": [f"{r['캠퍼스'][0]}캠 {r['패널']}({r['전압']}): {r['상태'] or '기록 없음'}"
                         + (f" / {r['비고']}" if r["비고"] else "") for _, r in un.iterrows()],
        "참고": "현재 패널당 단일 전류값만 기록됨(상별 R/S/T 값 없음)",
    }
