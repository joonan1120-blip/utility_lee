"""운전일지(일간 점검) 공통 로직: 판정, 입력 검증, 공기압축기 데이터 변환."""
from __future__ import annotations

from datetime import date

import pandas as pd

import calc

LOG_COLS = ["기록ID", "저장시각", "점검일", "점검시각", "캠퍼스", "설비명", "점검항목",
            "값", "판정", "점검자", "메모"]
COUNTER_POINTS = ("부하 시간", "무부하 시간")


def num(v):
    return calc._num(v)


def spec_text(it) -> str:
    lo, hi, u = it.get("하한", ""), it.get("상한", ""), it.get("단위", "")
    if it.get("입력형태") == "선택":
        return it.get("관리SPEC", "")
    if lo and hi:
        return f"{lo}~{hi} {u}".strip()
    if hi:
        return f"{hi} 이하 {u}".strip()
    if lo:
        return f"{lo} 이상 {u}".strip()
    return it.get("관리SPEC", "") or u


def judge(it, value) -> str:
    """정상 / 이탈 / 기록(판정 기준 없음) / '' (미입력)."""
    if value is None or str(value).strip() == "":
        return ""
    if it.get("입력형태") == "선택":
        ok = [s for s in str(it.get("정상값", "")).split(",") if s]
        if not ok:
            return "기록"
        return "정상" if str(value) in ok else "이탈"
    v = num(value)
    if v is None:
        return ""
    lo, hi = num(it.get("하한")), num(it.get("상한"))
    if lo is None and hi is None:
        return "기록"
    if (lo is not None and v < lo) or (hi is not None and v > hi):
        return "이탈"
    return "정상"


def compressor_frames(log: pd.DataFrame, comps: pd.DataFrame):
    """점검기록(long) → calc 모듈용 readings/equipment 형식."""
    eq = comps.rename(columns={"설비명": "호기"})
    if log.empty:
        return pd.DataFrame(columns=["측정일", "측정시각", "캠퍼스", "호기", "누적시간",
                                     "부하누적시간", "현재운전값"]), eq
    c = log[log["점검항목"].isin(COUNTER_POINTS + ("운전값",))]
    c = c.merge(comps[["캠퍼스", "설비명"]], on=["캠퍼스", "설비명"])
    if c.empty:
        return pd.DataFrame(columns=["측정일", "측정시각", "캠퍼스", "호기", "누적시간",
                                     "부하누적시간", "현재운전값"]), eq
    times = (c[c["점검항목"] == "부하 시간"].groupby(["캠퍼스", "설비명", "점검일"])["점검시각"].first())
    w = c.pivot_table(index=["캠퍼스", "설비명", "점검일"], columns="점검항목", values="값",
                      aggfunc="last").reset_index()
    for col in COUNTER_POINTS + ("운전값",):
        if col not in w:
            w[col] = ""
    w = w.join(times, on=["캠퍼스", "설비명", "점검일"])
    load = w["부하 시간"].map(num)
    unload = w["무부하 시간"].map(num)
    r = pd.DataFrame({
        "측정일": w["점검일"], "측정시각": w["점검시각"].fillna(""), "캠퍼스": w["캠퍼스"],
        "호기": w["설비명"],
        "누적시간": [None if (a is None or b is None) else a + b for a, b in zip(load, unload)],
        "부하누적시간": load, "현재운전값": w["운전값"].fillna(""),
    })
    return r[r["누적시간"].notna()].reset_index(drop=True), eq


def validate_group(items: pd.DataFrame, values: dict, log: pd.DataFrame, comps: pd.DataFrame,
                   campus: str, equip: str, d: date, t: str):
    """설비 1대 입력값 검증. values: {점검항목: 값}. (오류, 경고, 판정 dict) 반환."""
    errs, warns, judged = [], [], {}
    for _, it in items.iterrows():
        v = values.get(it["점검항목"])
        if v is None or str(v).strip() == "":
            continue
        if it["입력형태"] == "숫자" and num(v) is None:
            errs.append(f"{it['점검항목']}: 숫자를 입력하세요")
            continue
        judged[it["점검항목"]] = judge(it, v)
        if judged[it["점검항목"]] == "이탈":
            warns.append(f"{it['점검항목']} {v} — 관리 SPEC({spec_text(it)}) 이탈")
    crow = comps[(comps["캠퍼스"] == campus) & (comps["설비명"] == equip)]
    if not crow.empty and any(p in values for p in COUNTER_POINTS):
        load, unload = num(values.get("부하 시간")), num(values.get("무부하 시간"))
        if (load is None) != (unload is None):
            errs.append("부하 시간·무부하 시간은 함께 입력하세요 (가동률 계산용)")
        elif load is not None:
            eq_row = crow.iloc[0].to_dict()
            eq_row["호기"] = equip
            readings, _ = compressor_frames(log, comps)
            e2, w2 = calc.validate_entry(readings, eq_row, d, t, load + unload, load,
                                         values.get("운전값"))
            errs += [x.replace("누적시간", "총 가동시간(부하+무부하)") for x in e2]
            warns += w2
    if not crow.empty and crow.iloc[0]["형식"] == "인버터형" and "운전값" in values:
        mn, mx = num(crow.iloc[0]["최소운전값"]), num(crow.iloc[0]["최대운전값"])
        v = num(values["운전값"])
        if v is not None and v != 0 and mn is not None and mx is not None and not (mn <= v <= mx):
            msg = f"운전값은 0 또는 {mn:g}~{mx:g} {crow.iloc[0]['단위']} 범위여야 합니다"
            if msg not in errs:
                errs.append(msg)
    return errs, list(dict.fromkeys(warns)), judged


def today_status(items: pd.DataFrame, log: pd.DataFrame, d: date) -> pd.DataFrame:
    """해당 날짜 항목별 입력·판정 현황."""
    day = log[log["점검일"] == d.strftime("%Y-%m-%d")]
    m = items.merge(day[["캠퍼스", "설비명", "점검항목", "값", "판정", "점검자", "점검시각"]],
                    on=["캠퍼스", "설비명", "점검항목"], how="left")
    m["입력"] = m["값"].fillna("").astype(str).str.strip() != ""
    return m
