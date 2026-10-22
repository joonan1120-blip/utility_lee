"""저장소: 로컬 CSV(시연용) 또는 Google 시트(배포용).

Google 시트 탭 구성:
  점검항목 / 압축기설정 / 점검기록   (Apps Script 버전과 동일)
  정비기록 / 수변전패널 / 부하측정     (정비일지·월간 부하측정, Streamlit 판 추가)
"""
from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from logbook import LOG_COLS

KST = ZoneInfo("Asia/Seoul")
DATA = Path(__file__).parent / "data"
SEED = Path(__file__).parent / "data_seed"   # 저장소(GitHub)에 포함되는 초기 데이터
TABS = {"items": "점검항목", "comps": "압축기설정", "log": "점검기록",
        "maint": "정비기록", "panels": "수변전패널", "loads": "부하측정"}
FILES = {"items": "items.csv", "comps": "compressors.csv", "log": "log.csv",
         "maint": "maint.csv", "panels": "panels.csv", "loads": "loads.csv"}
MAINT_COLS = ["기록ID", "등록시각", "정비일", "캠퍼스", "분류", "설비", "정비내용", "비고", "주기일", "다음예정일", "등록자"]
LOAD_COLS = ["기록ID", "등록시각", "측정월", "측정일", "캠퍼스", "패널ID", "패널", "전압", "실부하A", "상태", "측정자", "메모"]
COLS = {"log": LOG_COLS, "maint": MAINT_COLS, "loads": LOAD_COLS}
KEYS = {"log": ["점검일", "캠퍼스", "설비명", "점검항목"], "maint": ["기록ID"], "loads": ["측정월", "패널ID"]}


def now_kst() -> datetime:
    return datetime.now(KST)


class CsvStore:
    name = "로컬 CSV (시연용 — Streamlit Cloud에서는 재시작 시 사라질 수 있음)"

    def read(self, key: str) -> pd.DataFrame:
        p = DATA / FILES[key]
        if not p.exists() and (SEED / FILES[key]).exists():   # 처음 실행: 초기 데이터 복사
            DATA.mkdir(exist_ok=True)
            p.write_bytes((SEED / FILES[key]).read_bytes())
        if not p.exists():
            return pd.DataFrame(columns=COLS.get(key, LOG_COLS))
        return pd.read_csv(p, dtype=str, keep_default_na=False)

    def upsert(self, key: str, rows: list[dict]):
        df = _merge(self.read(key), rows, COLS[key], KEYS[key])
        df.to_csv(DATA / FILES[key], index=False, encoding="utf-8-sig")

    def upsert_log(self, rows: list[dict]):
        self.upsert("log", rows)


class SheetStore:
    name = "Google 시트"

    def __init__(self, secrets):
        import gspread
        gc = gspread.service_account_from_dict(dict(secrets["gcp_service_account"]))
        self.sh = gc.open_by_url(secrets["sheet_url"])

    def _ws(self, key: str, create: bool = False):
        import gspread
        try:
            return self.sh.worksheet(TABS[key])
        except gspread.WorksheetNotFound:
            if not create:
                return None
            ws = self.sh.add_worksheet(TABS[key], rows=1000, cols=len(COLS[key]))
            ws.append_row(COLS[key], value_input_option="RAW")
            return ws

    def read(self, key: str) -> pd.DataFrame:
        ws = self._ws(key)
        vals = ws.get_all_values() if ws else []
        if not vals:
            return pd.DataFrame(columns=COLS.get(key, LOG_COLS))
        return pd.DataFrame(vals[1:], columns=vals[0])

    def upsert_log(self, rows: list[dict]):
        self.upsert("log", rows)

    def upsert(self, key: str, rows: list[dict]):
        ws = self._ws(key, create=True)
        vals = ws.get_all_values()
        header = vals[0]
        ki = [header.index(k) for k in KEYS[key]]
        idx = {tuple(r[i] for i in ki): n + 2 for n, r in enumerate(vals[1:])}
        updates, appends = [], []
        for r in rows:
            line = [str(r.get(c, "")) for c in header]
            k = tuple(str(r[c]) for c in KEYS[key])
            if k in idx:
                line[0] = vals[idx[k] - 1][0]  # 기록ID 유지
                updates.append({"range": f"A{idx[k]}", "values": [line]})
            else:
                appends.append(line)
        if updates:
            ws.batch_update(updates, value_input_option="RAW")
        if appends:
            ws.append_rows(appends, value_input_option="RAW")


def _merge(df: pd.DataFrame, rows: list[dict], cols=LOG_COLS, key=None) -> pd.DataFrame:
    """key가 같은 행은 교체(기록ID 유지), 없으면 추가."""
    key = key or KEYS["log"]
    new = pd.DataFrame(rows, columns=cols).fillna("").astype(str)
    for c in cols:
        if c not in df:
            df[c] = ""
    if not df.empty:
        if key != ["기록ID"]:
            old_ids = df.drop_duplicates(key, keep="last").set_index(key)["기록ID"]
            new["기록ID"] = [old_ids.get(tuple(r[k] for k in key) if len(key) > 1 else r[key[0]], r["기록ID"])
                           for _, r in new.iterrows()]
        df = df.merge(new[key], on=key, how="left", indicator=True)
        df = df[df["_merge"] == "left_only"].drop(columns="_merge")
    return pd.concat([df, new], ignore_index=True)[cols]


def new_id(prefix: str) -> str:
    return prefix + uuid.uuid4().hex[:10]


def make_rows(d: str, t: str, campus: str, equip: str, inspector: str,
              values: dict, judged: dict, memo: str) -> list[dict]:
    ts = now_kst().strftime("%Y-%m-%d %H:%M:%S")
    return [dict(기록ID="R" + uuid.uuid4().hex[:10], 저장시각=ts, 점검일=d, 점검시각=t,
                 캠퍼스=campus, 설비명=equip, 점검항목=k, 값=str(v), 판정=judged.get(k, ""),
                 점검자=inspector, 메모=memo)
            for k, v in values.items()]


def get_store(secrets):
    try:
        if "gcp_service_account" in secrets and "sheet_url" in secrets:
            return SheetStore(secrets)
    except Exception:  # secrets 파일 없음 등 → 로컬 CSV
        pass
    return CsvStore()
