"""사번 로그인. 사번 목록은 config/users.csv (GitHub에 재업로드해서 관리, 실시간 연동 아님).

users.csv 열
  사번  : 숫자 7자리 (엑셀에서 앞자리 0이 지워져도 7자리로 맞춰서 비교)
  이름  : 화면과 점검기록에 표시되는 이름
  권한  : 입력자 / 공용 / 관리자
          - 입력자: 점검자 이름이 자동 기록됨
          - 공용  : 여러 명이 쓰는 계정. 입력할 때 점검자 이름을 직접 적음
          - 관리자: 입력자 권한 + 🗂 데이터 메뉴
  사용  : Y 면 로그인 가능, N 이면 차단 (퇴사·이동 시 N)
  PIN   : 비워두면 사번만으로 로그인. 값이 있으면 그 PIN도 맞아야 로그인
  비고  : 메모
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

USERS_FILE = Path(__file__).parent / "config" / "users.csv"
ROLES = ("입력자", "공용", "관리자")


def norm(no) -> str:
    s = str(no).strip().replace(" ", "")
    if s.endswith(".0"):
        s = s[:-2]
    return s.zfill(7) if s.isdigit() and len(s) < 7 else s


def load_users() -> pd.DataFrame | None:
    if not USERS_FILE.exists():
        return None
    for enc in ("utf-8-sig", "cp949"):          # 엑셀에서 저장하면 cp949일 수 있음
        try:
            df = pd.read_csv(USERS_FILE, dtype=str, keep_default_na=False, encoding=enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        return None
    df.columns = [c.strip() for c in df.columns]
    for c in ("사번", "이름", "권한", "사용", "PIN", "비고"):
        if c not in df:
            df[c] = ""
    df["사번"] = df["사번"].map(norm)
    df["권한"] = df["권한"].str.strip().where(df["권한"].str.strip().isin(ROLES), "입력자")
    df["사용"] = df["사용"].str.strip().str.upper().replace("", "Y")
    return df


def authenticate(no: str, pin: str = ""):
    """(사용자 dict, None) 또는 (None, 오류 메시지)."""
    df = load_users()
    if df is None:
        return None, "사번 목록 파일(config/users.csv)을 읽을 수 없습니다"
    r = df[df["사번"] == str(no).strip().replace(" ", "")]   # 입력은 7자리 그대로 비교
    if r.empty:
        return None, "등록되지 않은 사번입니다"
    u = r.iloc[0]
    if u["사용"] != "Y":
        return None, "사용이 중지된 사번입니다. 관리자에게 문의하세요"
    if str(u["PIN"]).strip() and str(pin).strip() != str(u["PIN"]).strip():
        return None, "PIN이 맞지 않습니다"
    return {"사번": u["사번"], "이름": u["이름"].strip() or u["사번"], "권한": u["권한"]}, None
