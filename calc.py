"""공기압축기 가동 계산 로직 (엑셀 '공기압축기_운용현황.xlsx' 규칙 이식).

- 가동률 = 기간 부하시간 증가분 ÷ 총 가동시간 증가분
- 구간 계산시간 = MAX(측정 경과시간, 총 운전시간 증가분)  (시간 자동 보정)
- 환산 사용시간: 일반형 = 부하 증가분, 인버터 = 부하 증가분 × 구간 추정 로드율
  · 구간 로드율 = 양 끝 '운전 중(>0)' 로드율 평균 (한쪽만 있으면 그 값)
  · 부하 증가 0 → 0
  · 양 끝 모두 정지(0)인데 부하가 증가 → 최소값/최대값 비율 (최소값 추정)
- 기간 용량 사용률 = Σ환산 사용시간 ÷ Σ구간 계산시간
- 최근 측정 사용률: 일반형 = 직전 구간 부하증가÷총증가, 인버터 = 현재값÷최대값
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

import pandas as pd

EPS = 1e-7


def _num(v):
    try:
        if v is None or (isinstance(v, str) and v.strip() == ""):
            return None
        f = float(v)
        return None if pd.isna(f) else f
    except (TypeError, ValueError):
        return None


def prepare(readings: pd.DataFrame) -> pd.DataFrame:
    """문자열 시트 값을 계산용 컬럼으로 변환."""
    df = readings.copy()
    if df.empty:
        for c in ["d", "dt", "tot", "load", "run"]:
            df[c] = pd.Series(dtype="object")
        return df
    df["d"] = pd.to_datetime(df["측정일"], errors="coerce").dt.date
    t = df["측정시각"].fillna("").astype(str).str.strip()
    has_t = t.str.match(r"^\d{1,2}:\d{2}$")
    dt_str = df["측정일"].astype(str) + " " + t.where(has_t, "00:00")
    df["dt"] = pd.to_datetime(dt_str, errors="coerce")
    df["has_time"] = has_t
    df["tot"] = df["누적시간"].map(_num)
    df["load"] = df["부하누적시간"].map(_num)
    df["run"] = df["현재운전값"].map(_num)
    df = df[df["d"].notna() & df["tot"].notna() & df["load"].notna()]
    return df.sort_values(["캠퍼스", "호기", "dt"]).reset_index(drop=True)


def _eq_lookup(equipment: pd.DataFrame) -> dict:
    out = {}
    for _, e in equipment.iterrows():
        out[(e["캠퍼스"], e["호기"])] = {
            "형식": e["형식"],
            "min": _num(e.get("최소운전값")),
            "max": _num(e.get("최대운전값")),
            "단위": e.get("단위", "") or "",
        }
    return out


def _inv_ratio(run, eq):
    """인버터 시점 로드율. 범위 밖/미입력이면 None."""
    if run is None or eq["max"] in (None, 0):
        return None
    if run == 0:
        return 0.0
    if eq["min"] is not None and not (eq["min"] <= run <= eq["max"]):
        return None
    return run / eq["max"]


def segments(df: pd.DataFrame, equipment: pd.DataFrame) -> pd.DataFrame:
    """호기별 연속 측정 구간 계산."""
    eqs = _eq_lookup(equipment)
    rows = []
    for (camp, unit), g in df.groupby(["캠퍼스", "호기"], sort=False):
        eq = eqs.get((camp, unit))
        if eq is None:
            continue
        g = g.reset_index(drop=True)
        for i in range(1, len(g)):
            p, c = g.iloc[i - 1], g.iloc[i]
            if p["has_time"] and c["has_time"]:
                elapsed = (c["dt"] - p["dt"]).total_seconds() / 3600
            else:
                elapsed = (pd.Timestamp(c["d"]) - pd.Timestamp(p["d"])).total_seconds() / 3600
            d_tot = c["tot"] - p["tot"]
            d_load = c["load"] - p["load"]
            calc_h = max(elapsed, d_tot)
            status = "정상"
            if d_tot < 0 or d_load < 0:
                status = "누적값 감소"
            elif d_load > d_tot:
                status = "부하증가량 확인"
            seg_ratio, used, cap = None, None, "정상"
            if status == "정상":
                if eq["형식"] == "일반형":
                    seg_ratio = 1.0
                else:
                    q_c = _inv_ratio(c["run"], eq)
                    q_p = _inv_ratio(p["run"], eq)
                    z = q_p if (q_p is not None and q_p > 0) else None
                    if d_load == 0:
                        seg_ratio = 0.0
                    elif q_c is not None and q_c > 0:
                        seg_ratio = (q_c + z) / 2 if z is not None else q_c
                    elif z is not None:
                        seg_ratio = z
                    elif q_c == 0 and q_p == 0 and eq["min"] and eq["max"]:
                        seg_ratio = eq["min"] / eq["max"]
                        cap = "최소값 추정"
                    else:
                        cap = "운전값 부족"
                if seg_ratio is not None:
                    used = 0.0 if d_load == 0 else d_load * seg_ratio
            else:
                cap = status
            rows.append(
                dict(캠퍼스=camp, 호기=unit, 형식=eq["형식"], 시작일=p["d"], 종료일=c["d"],
                     경과=elapsed, 총증가=d_tot, 부하증가=d_load, 계산시간=calc_h,
                     보정=max(calc_h - elapsed, 0) if calc_h - elapsed > EPS else 0.0,
                     구간로드율=seg_ratio, 환산=used, 상태=status, 용량상태=cap)
            )
    return pd.DataFrame(rows)


@dataclass
class Result:
    units: pd.DataFrame
    campus: pd.DataFrame
    segs: pd.DataFrame


def compute(readings: pd.DataFrame, equipment: pd.DataFrame,
            start: date | None = None, end: date | None = None) -> Result:
    df_all = prepare(readings)
    eq = equipment[equipment.get("사용", 1).astype(str) != "0"] if "사용" in equipment else equipment
    df = df_all
    if start:
        df = df[df["d"] >= start]
    if end:
        df = df[df["d"] <= end]
    segs = segments(df, eq)
    eqs = _eq_lookup(eq)

    urows = []
    for _, e in eq.iterrows():
        camp, unit = e["캠퍼스"], e["호기"]
        g = df[(df["캠퍼스"] == camp) & (df["호기"] == unit)]
        ga = df_all[(df_all["캠퍼스"] == camp) & (df_all["호기"] == unit)]
        s = segs[(segs["캠퍼스"] == camp) & (segs["호기"] == unit)] if not segs.empty else segs
        r = dict(캠퍼스=camp, 호기=unit, 형식=e["형식"], 건수=len(g),
                 시작일=None, 종료일=None, 기간총=None, 기간부하=None, 가동률=None,
                 용량사용률=None, 최근로드율=None, 최근운전값=None, 최근측정일=None, 상태="")
        if len(ga):
            last = ga.iloc[-1]
            r["최근측정일"] = last["d"]
            if e["형식"] == "인버터형":
                r["최근운전값"] = last["run"]
                r["최근로드율"] = _inv_ratio(last["run"], eqs[(camp, unit)])
            elif len(ga) >= 2:
                prev = ga.iloc[-2]
                dt_ = last["tot"] - prev["tot"]
                r["최근로드율"] = 0.0 if dt_ == 0 else (last["load"] - prev["load"]) / dt_
        if len(g) < 2:
            r["상태"] = "기간 내 2회 입력 필요" if len(g) else "미입력"
        elif (s["상태"] != "정상").any():
            r["상태"] = "입력 오류 확인"
        else:
            r.update(시작일=g.iloc[0]["d"], 종료일=g.iloc[-1]["d"],
                     기간총=g.iloc[-1]["tot"] - g.iloc[0]["tot"],
                     기간부하=g.iloc[-1]["load"] - g.iloc[0]["load"])
            r["가동률"] = r["기간부하"] / r["기간총"] if r["기간총"] else None
            if (s["용량상태"] == "운전값 부족").any():
                r["상태"] = "운전값 부족"
            else:
                ch = s["계산시간"].sum()
                r["용량사용률"] = s["환산"].sum() / ch if ch else None
                r["상태"] = "최소값 추정" if (s["용량상태"] == "최소값 추정").any() else "정상"
        r["계산시간"] = s["계산시간"].sum() if len(s) else 0
        r["환산"] = s["환산"].sum() if len(s) else 0
        urows.append(r)
    units = pd.DataFrame(urows)

    crows = []
    for camp, u in units.groupby("캠퍼스", sort=False):
        ok = u["가동률"].notna()
        c = dict(캠퍼스=camp, 대수=len(u), 입력호기=int((u["건수"] > 0).sum()))
        c["기간총"] = u.loc[ok, "기간총"].sum() if ok.all() else None
        c["기간부하"] = u.loc[ok, "기간부하"].sum() if ok.all() else None
        c["가동률"] = c["기간부하"] / c["기간총"] if c["기간총"] else None
        cap_ok = u["용량사용률"].notna().all()
        c["용량사용률"] = (u["환산"].sum() / u["계산시간"].sum()) if cap_ok and u["계산시간"].sum() else None
        c["최근사용률"] = u["최근로드율"].sum() / len(u) if u["최근로드율"].notna().all() else None
        c["최근측정일"] = u["최근측정일"].dropna().max() if u["최근측정일"].notna().any() else None
        c["시작일"] = u["시작일"].dropna().min() if u["시작일"].notna().any() else None
        c["종료일"] = u["종료일"].dropna().max() if u["종료일"].notna().any() else None
        c["보정시간"] = segs.loc[segs["캠퍼스"] == camp, "보정"].sum() if not segs.empty else 0
        bad = u[~u["상태"].isin(["정상", "최소값 추정"])]
        if bad.empty:
            c["상태"] = "정상"
        elif (u["건수"] == 0).all():
            c["상태"] = "미입력"
        else:
            c["상태"] = "확인 필요: " + ", ".join(f"{h} {s}" for h, s in zip(bad["호기"], bad["상태"]))
        crows.append(c)
    return Result(units=units, campus=pd.DataFrame(crows), segs=segs)


def daily_trend(segs: pd.DataFrame) -> pd.DataFrame:
    """구간 종료일 기준 캠퍼스별 가동률·용량 사용률 추이."""
    if segs.empty:
        return pd.DataFrame(columns=["캠퍼스", "날짜", "가동률", "용량사용률"])
    s = segs[segs["상태"] == "정상"]
    g = s.groupby(["캠퍼스", "종료일"]).agg(부하=("부하증가", "sum"), 총=("총증가", "sum"),
                                         환산=("환산", "sum"), 시간=("계산시간", "sum")).reset_index()
    g["가동률"] = g["부하"] / g["총"].where(g["총"] > 0)
    g["용량사용률"] = g["환산"] / g["시간"].where(g["시간"] > 0)
    return g.rename(columns={"종료일": "날짜"})[["캠퍼스", "날짜", "가동률", "용량사용률"]]


# ---------------- 입력 검증 ----------------

def validate_entry(readings: pd.DataFrame, eq_row: dict, d: date, t: str,
                   tot, load, run, overwrite: bool = True) -> tuple[list[str], list[str]]:
    """새 측정값 검증. (오류 목록, 경고 목록) 반환. 오류가 있으면 저장 불가."""
    errs, warns = [], []
    tot, load, run = _num(tot), _num(load), _num(run)
    if tot is None or load is None:
        errs.append("누적시간·부하누적시간을 모두 입력하세요")
        return errs, warns
    if tot < 0 or load < 0:
        errs.append("음수는 입력할 수 없습니다")
    if load > tot:
        errs.append("부하누적시간이 누적시간보다 큽니다")
    inv = eq_row["형식"] == "인버터형"
    mn, mx = _num(eq_row.get("최소운전값")), _num(eq_row.get("최대운전값"))
    if inv:
        if run is None:
            errs.append(f"현재 운전값({eq_row.get('단위','')})을 입력하세요 (정지 시 0)")
        elif run != 0 and mn is not None and mx is not None and not (mn <= run <= mx):
            errs.append(f"운전값은 0 또는 {mn:g}~{mx:g} {eq_row.get('단위','')} 범위여야 합니다")
    df = prepare(readings)
    g = df[(df["캠퍼스"] == eq_row["캠퍼스"]) & (df["호기"] == eq_row["호기"])]
    if (g["d"] == d).any():
        if not overwrite:
            errs.append(f"{d:%Y-%m-%d} 측정값이 이미 있습니다 (측정일 중복)")
            return errs, warns
        g = g[g["d"] != d]  # 같은 날 재입력 = 수정
    new_dt = pd.Timestamp(datetime.combine(d, datetime.strptime(t or "00:00", "%H:%M").time()))
    prev = g[g["dt"] < new_dt].tail(1)
    nxt = g[g["dt"] > new_dt].head(1)
    if not prev.empty:
        p = prev.iloc[0]
        d_tot, d_load = tot - p["tot"], load - p["load"]
        if d_tot < 0:
            errs.append(f"누적시간이 직전값({p['tot']:,.0f}h)보다 작습니다")
        if d_load < 0:
            errs.append(f"부하누적시간이 직전값({p['load']:,.0f}h)보다 작습니다")
        if d_tot >= 0 and d_load > d_tot:
            errs.append(f"부하 증가({d_load:,.0f}h)가 총시간 증가({d_tot:,.0f}h)보다 큽니다")
        elapsed = (new_dt - p["dt"]).total_seconds() / 3600
        if d_tot > elapsed + 1:
            warns.append(f"총시간 증가 {d_tot:,.0f}h > 경과 {elapsed:,.1f}h — 계기값·시각 확인 (계산 시 자동 보정)")
    if not nxt.empty:
        n = nxt.iloc[0]
        if tot > n["tot"] or load > n["load"]:
            errs.append(f"이후 측정값({n['d']:%Y-%m-%d})보다 큽니다")
        warns.append("최신 측정일보다 이전 날짜입니다")
    return errs, warns
