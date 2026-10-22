"""총괄(마스터) + 담당자 4명(전기·항온항습기·공기압축기·기타 설비) 에이전트.

흐름
  1) 코드가 분야별 데이터(이번 주 운전일지, 정비이력, 부하측정)를 잘라 담당자 4명에게 병렬로 보냄
  2) 담당자는 정해진 JSON 보고서로 답함
  3) 총괄이 보고서를 통합해 브리핑(스트리밍) → 이후 질문은 도구로 담당자 재호출·정비일지 검색·등록 제안
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

import briefing as bf
import logbook as lb
import ops

PDIR = Path(__file__).parent / "prompts" / "agents"
DOMAINS = {
    "전기": dict(file="electric.md", points=("전류", "전압"), maint=["전기/수변전"], comp=False, electric=True),
    "항온항습기": dict(file="hvac.md", types=["항온항습기", "실내 환경"], maint=["항온항습기"]),
    "공기압축기": dict(file="compressor.md", types=["공기압축기", "에어탱크", "냉동식 드라이어", "흡착식 드라이어"],
                  maint=["공기압축기"], comp=True),
    "기타 설비": dict(file="facility.md", types=["진공설비", "폐수처리", "CDU(순수)"],
                  maint=["DI 발생기", "진공펌프", "에어샤워", "기타"]),
}
NAMES = list(DOMAINS)
CAMPUSES = ["1캠퍼스", "2캠퍼스", "3캠퍼스"]

_S = {"type": "string"}
REPORT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["담당", "요약", "이슈", "정비예정", "확인불가"],
    "properties": {
        "담당": _S, "요약": _S,
        "이슈": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["등급", "캠퍼스", "설비", "항목", "근거", "상태분류", "원인추정", "권고", "관련분야"],
            "properties": {"등급": {"type": "string", "enum": ["긴급", "주의", "관찰"]}, "캠퍼스": _S, "설비": _S,
                           "항목": _S, "근거": _S, "상태분류": _S, "원인추정": _S, "권고": _S,
                           "관련분야": {"type": "array", "items": {"type": "string", "enum": NAMES}}}}},
        "정비예정": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["설비", "내용", "예정일", "상태"],
            "properties": {"설비": _S, "내용": _S, "예정일": _S,
                           "상태": {"type": "string", "enum": ["지연", "임박", "예정"]}}}},
        "확인불가": {"type": "array", "items": _S},
    },
}


# ---------------- 데이터 분배 ----------------
def domain_context(name: str, D: dict, now: datetime, include_names: bool = False) -> dict:
    cfg = DOMAINS[name]
    items = D["items"]
    if "types" in cfg:
        sub = items[items["설비유형"].isin(cfg["types"])]
    else:
        sub = items[items["점검항목"].isin(cfg["points"])]
    comps = D["comps"] if cfg.get("comp") else D["comps"].iloc[0:0]
    wk = bf.build_weekly_context(sub, D["log"], comps, now, include_names)
    ctx = {"담당": name, "기준시각": wk["기준시각"], "이번주": wk["이번주"],
           "운전일지_이번주": {k: wk[k] for k in ("입력현황", "이탈", "경계근접", "급변", "메모")},
           "정비": ops.maint_for(D["maint"], cfg["maint"], now.date())}
    if cfg.get("comp"):
        ctx["공기압축기_가동"] = wk["공기압축기"]
        ctx["공기압축기_기준"] = wk["공기압축기_기준"]
    if cfg.get("electric"):
        ctx["부하측정"] = ops.electric_context(D["panels"], D["loads"], now.date())
    return ctx


def sub_system(name: str, ctx: dict) -> str:
    return ((PDIR / "common.md").read_text(encoding="utf-8") + "\n\n" +
            (PDIR / DOMAINS[name]["file"]).read_text(encoding="utf-8") +
            "\n\n# 데이터\n" + json.dumps(ctx, ensure_ascii=False, indent=1, default=str))


def _client(api_key):
    from openai import OpenAI
    return OpenAI(api_key=api_key)


def call_sub(api_key: str, model: str, name: str, ctx: dict) -> dict:
    """담당자 보고서(JSON) 생성. 실패해도 예외 대신 실패 보고서를 반환."""
    client, system = _client(api_key), sub_system(name, ctx)
    ask = "이번 주 담당 분야 보고서를 JSON으로 작성하라."
    try:
        try:
            r = client.responses.create(model=model, instructions=system, input=ask,
                                        text={"format": {"type": "json_schema", "name": "report",
                                                         "schema": REPORT_SCHEMA, "strict": True}})
        except Exception:  # 구조화 출력 미지원 모델 → 일반 JSON 요청
            r = client.responses.create(model=model, instructions=system + "\n\n출력 스키마:\n" +
                                        json.dumps(REPORT_SCHEMA, ensure_ascii=False), input=ask)
        txt = r.output_text.strip()
        if txt.startswith("```"):
            txt = txt.strip("`").split("\n", 1)[1]
        rep = json.loads(txt)
        rep["담당"] = name
        return rep
    except Exception as e:
        return {"담당": name, "요약": f"보고 실패: {e}", "이슈": [], "정비예정": [], "확인불가": ["담당자 보고 실패"]}


def collect_reports(api_key, model, ctxs: dict, on_done=None) -> dict:
    out = {}
    with ThreadPoolExecutor(max_workers=len(ctxs)) as ex:
        futs = {ex.submit(call_sub, api_key, model, n, c): n for n, c in ctxs.items()}
        for f in as_completed(futs):
            out[futs[f]] = f.result()
            if on_done:
                on_done(futs[f], out[futs[f]])
    return {n: out[n] for n in ctxs}


def master_system(reports: dict, overview: dict) -> str:
    return ((PDIR / "master.md").read_text(encoding="utf-8")
            .replace("{reports_json}", json.dumps(list(reports.values()), ensure_ascii=False, indent=1))
            .replace("{overview_json}", json.dumps(overview, ensure_ascii=False, indent=1, default=str)))


def overview(D: dict, now: datetime, include_names=False) -> dict:
    wk = bf.build_weekly_context(D["items"], D["log"], D["comps"], now, include_names)
    n = D["items"].groupby("캠퍼스").size()
    tot = sum(n.get(e["캠퍼스"], 0) for e in wk["입력현황"])
    overall = sum(e["이번주_입력률"] * n.get(e["캠퍼스"], 0) for e in wk["입력현황"]) / tot if tot else 0
    return {"기준시각": wk["기준시각"], "이번주": wk["이번주"], "지난주": wk["지난주"],
            "전체_이번주_입력률": round(overall, 3),
            "입력률_기준": "입력 항목 수 ÷ (점검항목 수 × 이번 주 점검일수), 캠퍼스별 항목 수 가중",
            "입력현황": [{"캠퍼스": e["캠퍼스"], "이번주_입력률": e["이번주_입력률"], "일자별": e["일자별"]}
                     for e in wk["입력현황"]]}


# ---------------- 후속 대화 도구 ----------------
def _t(name, desc, props, req=None):
    return {"type": "function", "name": name, "description": desc, "strict": True,
            "parameters": {"type": "object", "properties": props, "required": req or list(props),
                           "additionalProperties": False}}


TOOLS = [
    _t("ask_specialist", "담당자에게 세부 질문을 하고 답을 받는다. 담당자는 자기 분야 데이터를 갖고 있다.",
       {"specialist": {"type": "string", "enum": NAMES}, "question": _S}),
    _t("search_maintenance", "정비일지를 검색한다. keyword는 설비명·정비내용 일부(없으면 빈 문자열).",
       {"keyword": _S, "category": {"type": "string", "enum": [""] + ops.MAINT_CATS},
        "campus": {"type": "string", "enum": [""] + CAMPUSES}, "days": {"type": "integer"}}),
    _t("get_item_trend", "운전일지 항목의 일자별 값을 조회한다(최대 120일).",
       {"campus": {"type": "string", "enum": CAMPUSES}, "equipment": _S, "point": _S, "days": {"type": "integer"}}),
    _t("propose_maintenance_record", "정비일지 등록을 제안한다. 사용자가 화면에서 저장을 눌러야 확정된다. "
       "date는 YYYY-MM-DD(모르면 빈 문자열), cycle_days는 주기 정비 일수(없으면 0).",
       {"campus": {"type": "string", "enum": CAMPUSES}, "category": {"type": "string", "enum": ops.MAINT_CATS},
        "equipment": _S, "work": _S, "date": _S, "note": _S, "cycle_days": {"type": "integer"}}),
]


def tool_impl(api_key, model, D, ctxs, reports, now, on_propose):
    today = now.date()

    def ask_specialist(specialist, question):
        if specialist not in ctxs:
            return {"error": "알 수 없는 담당"}
        sysm = sub_system(specialist, ctxs[specialist]) + "\n\n# 이번 주 내 보고서\n" + \
            json.dumps(reports.get(specialist, {}), ensure_ascii=False)
        sysm += "\n\n총괄의 질문에 근거 수치와 함께 5줄 이내 한국어로 답하라(JSON 아님)."
        r = _client(api_key).responses.create(model=model, instructions=sysm, input=question)
        return {"담당": specialist, "답변": r.output_text}

    def search_maintenance(keyword, category, campus, days):
        m = ops.maint_status(D["maint"], today)
        days = max(1, min(int(days or 365), 3650))
        m = m[m["정비일"] >= (today - timedelta(days=days)).isoformat()]
        if category:
            m = m[m["분류"] == category]
        if campus:
            m = m[m["캠퍼스"] == campus]
        if keyword:
            k = keyword.replace(" ", "")
            m = m[(m["설비"].str.replace(" ", "").str.contains(k, regex=False)) |
                  (m["정비내용"].str.replace(" ", "").str.contains(k, regex=False))]
        cols = ["정비일", "캠퍼스", "분류", "설비", "정비내용", "비고", "다음예정일", "예정상태"]
        return {"건수": int(len(m)), "기록": m.sort_values("정비일")[cols].tail(30).to_dict("records")}

    def get_item_trend(campus, equipment, point, days):
        days = max(1, min(int(days or 30), 120))
        g = D["log"][(D["log"]["캠퍼스"] == campus) & (D["log"]["설비명"] == equipment) &
                     (D["log"]["점검항목"] == point) &
                     (D["log"]["점검일"] >= (today - timedelta(days=days)).isoformat())]
        it = D["items"][(D["items"]["캠퍼스"] == campus) & (D["items"]["설비명"] == equipment) &
                        (D["items"]["점검항목"] == point)]
        if it.empty:
            names = sorted(set(D["items"][D["items"]["캠퍼스"] == campus]["설비명"]))
            return {"error": "해당 설비·항목 없음", "이 캠퍼스 설비 목록": names}
        return {"SPEC": lb.spec_text(it.iloc[0]), "단위": it.iloc[0]["단위"],
                "기록": g.sort_values("점검일")[["점검일", "값", "판정"]].to_dict("records")}

    def propose_maintenance_record(campus, category, equipment, work, date, note, cycle_days):
        d = date or today.isoformat()
        cyc = int(cycle_days or 0)
        rec = {"캠퍼스": campus, "분류": category, "설비": equipment, "정비내용": work, "정비일": d, "비고": note,
               "주기일": str(cyc) if cyc else "",
               "다음예정일": (pd.Timestamp(d) + pd.Timedelta(days=cyc)).date().isoformat() if cyc else ""}
        on_propose(rec)
        return {"상태": "사용자 확인 대기 (화면의 저장 버튼)", "제안": rec}

    return dict(ask_specialist=ask_specialist, search_maintenance=search_maintenance,
                get_item_trend=get_item_trend, propose_maintenance_record=propose_maintenance_record)


def chat_turn(api_key, model, system, history, impl, on_tool=None, max_steps=5) -> str:
    client = _client(api_key)
    seq = [{"role": m["role"], "content": m["content"]} for m in history]
    r = None
    for _ in range(max_steps):
        r = client.responses.create(model=model, instructions=system, input=seq, tools=TOOLS)
        calls = [o for o in r.output if getattr(o, "type", "") == "function_call"]
        if not calls:
            return r.output_text
        seq += [o.model_dump(exclude_none=True) if hasattr(o, "model_dump") else o for o in r.output]
        for c in calls:
            if on_tool:
                on_tool(c.name, c.arguments)
            try:
                out = impl[c.name](**json.loads(c.arguments))
            except Exception as e:
                out = {"error": str(e)}
            seq.append({"type": "function_call_output", "call_id": c.call_id,
                        "output": json.dumps(out, ensure_ascii=False, default=str)})
    return (r.output_text if r is not None else "") or "조회가 길어져 답을 마치지 못했습니다. 질문을 나눠서 해 주세요."


# ---------------- API 키 없을 때 ----------------
def fallback(D: dict, now: datetime) -> str:
    wk = bf.build_weekly_context(D["items"], D["log"], D["comps"], now)
    out = [bf.fallback_report(wk)]
    e = ops.electric_context(D["panels"], D["loads"], now.date())
    if isinstance(e, dict) and "최신측정월" in e:
        out.append(f"\n**⚡ 수변전 부하측정** (최신 {e['최신측정월']}, {e['측정완료']}/{e['패널수']} 판넬)")
        hi = e["부하율_80이상"]
        out.append(f"- 부하율 80% 이상: {len(hi)}개" + (": " + ", ".join(
            f"{r['캠퍼스'][0]}캠 {r['패널']} {r['부하율'] * 100:.0f}%" for r in hi[:5]) if hi else ""))
        if not e["이번달_측정여부"]:
            out.append(f"- 이번 달({now:%Y-%m}) 부하측정 기록 없음")
    due = ops.maint_status(D["maint"], now.date())
    due = due[due["예정상태"].isin(["지연", "임박"])]
    if len(due):
        out.append("\n**🔧 정비 지연·임박**")
        out += [f"- {r['캠퍼스'][0]}캠 {r['설비']} · {r['정비내용']} — 예정 {r['다음예정일']} ({r['예정상태']})"
                for _, r in due.iterrows()]
    return "\n".join(out)
