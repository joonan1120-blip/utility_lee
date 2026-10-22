# Utility Agent — 유틸리티 운전일지 · 정비 · AI 브리핑

반도체 패키징·테스트 공장 1·2·3캠퍼스의 유틸리티 설비를 관리하는 웹앱입니다. 현장 점검자가 휴대폰으로 운전일지를 입력하면 관리 SPEC 이탈을 자동으로 판정하고, 공기압축기 가동률·정비 일정·수변전 부하를 한 화면에서 보여줍니다. OpenAI API 키를 설정하면 총괄 + 담당자 4명 구조의 AI 주간 브리핑도 쓸 수 있습니다.

## 주요 기능

| 메뉴 | 내용 |
|---|---|
| 📝 입력 | 운전일지 379개 항목을 설비 단위로 입력. SPEC 정상/이탈 즉시 표시, 직전값 표시, 같은 날 재입력 = 수정 |
| 🤖 브리핑 | 총괄 + 담당 4명(전기·항온항습기·공기압축기·기타 설비)의 이번 주 보고와 대화형 질의. 대화로 정비일지 등록 제안(저장 버튼으로 확정) |
| 📊 현황 | 캠퍼스별 입력률, SPEC 이탈 목록, 미완료 설비, 항목별 60일 추이 |
| ⚙️ 압축기 | 가동률·기간 용량 사용률·최근 측정 사용률 (기존 엑셀 계산 규칙과 결과 일치) |
| 🔧 정비 | 정비일지 기록·검색, 주기 정비 다음 예정일, 지연·임박 표시 |
| ⚡ 부하측정 | 월간 수변전 판넬 부하 입력, 부하율(실부하 ÷ 차단기 정격), 전월 대비, 80% 이상 경고 |
| 🗂 데이터 | CSV 내려받기, 항목·설정 확인 |

## 빠른 시작 (Windows)

1. **Python 3.12.10 설치:** [python-3.12.10-amd64.exe](https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe). 설치할 때 "Add python.exe to PATH"를 체크합니다.
2. **설정 파일 만들기:** `.env.example`을 복사해서 이름을 `.env`로 바꾸고 `OPENAI_API_KEY`를 채웁니다. 키가 없으면 AI 대신 규칙 기반 요약만 표시됩니다.
3. **실행:** `실행.bat`을 더블클릭합니다.
   - 처음에는 패키지가 자동으로 설치됩니다.
   - 8765번부터 비어 있는 포트를 찾아 서버를 켭니다.
4. **접속:** PC 브라우저는 `http://localhost:8765`로 접속합니다. 휴대폰은 같은 와이파이에서 `http://<PC IP>:8765`로 접속합니다. PIN은 `.env`의 `APP_PIN` 값입니다(기본값 1234).

다른 운영체제나 직접 실행할 때는 아래 명령을 씁니다.

```bash
pip install -r requirements.txt
streamlit run app.py --server.address 0.0.0.0 --server.port 8765
```

## 사번 로그인 (`config/users.csv`)

로그인은 사번으로 합니다. 사번 목록은 실시간으로 연동하지 않습니다. **이 파일을 고친 뒤 GitHub에 다시 올리고, 서버를 재시작하면 반영됩니다.**

| 열 | 내용 |
|---|---|
| 사번 | 7자리 숫자. 엑셀에서 앞자리 0이 지워져도 7자리로 맞춰서 읽습니다 |
| 이름 | 화면과 점검기록에 남는 이름 |
| 권한 | `입력자`: 이름 자동 기록 / `공용`: 입력할 때 점검자 이름을 직접 적음(기록에는 "이름(공용)") / `관리자`: 입력자 권한 + 🗂 데이터 메뉴 |
| 사용 | `Y` 로그인 가능, `N` 차단 (퇴사·이동 시) |
| PIN | 비우면 사번만으로 로그인, 값을 넣으면 PIN도 확인 |
| 비고 | 메모 |

- **샘플 계정:** `2609001`(이준환, 입력자), `0000000`(공용), `1234567`(관리자)
- **엑셀 편집:** CSV(UTF-8) 또는 CSV(쉼표로 분리) 형식으로 저장하면 됩니다. 두 형식 모두 읽습니다.
- **보안:** 사번만 알면 로그인할 수 있으므로, 필요한 계정에는 PIN을 넣으세요. 이름과 사번이 들어 있으니 저장소는 비공개로 유지하세요.
- **파일이 없을 때:** `config/users.csv`가 없으면 예전처럼 공용 PIN(`APP_PIN`) 로그인으로 동작합니다.

## 설정

설정은 아래 순서로 찾습니다.

1. `.streamlit/secrets.toml` (값이 비어 있지 않을 때)
2. 환경변수 또는 `.env`
3. 기본값

| `.env` | `secrets.toml` | 설명 |
|---|---|---|
| `OPENAI_API_KEY` | `openai_api_key` | OpenAI API 키 |
| `OPENAI_MODEL` | `openai_model` | 기본값 `gpt-6.1-sol` |
| `APP_PIN` | `pin` | 공용 PIN (`config/users.csv`가 없을 때만 사용) |
| `BRIEFING_INCLUDE_NAMES` | `briefing_include_names` | `true`면 AI에 보내는 메모에 점검자 이름 포함 (기본 `false`) |
| — | `sheet_url`, `[gcp_service_account]` | Google 시트 저장 (`secrets.toml.example` 참고) |

`.env`와 `.streamlit/secrets.toml`은 `.gitignore`에 들어 있어서 저장소에 올라가지 않습니다.

## 데이터

- **`data_seed/`** (저장소에 포함되는 초기 데이터)

  | 파일 | 내용 |
  |---|---|
  | `items.csv` | 점검항목·SPEC |
  | `compressors.csv` | 압축기 설정 |
  | `panels.csv` | 수변전 판넬 |
  | `log.csv` | 2026-10-13~20 압축기 측정값 |
  | `maint.csv` | 9월 정비일지 |
  | `loads.csv` | 9월 실부하 |

- **`data/`** (실제 운영 데이터, 저장소 제외): 처음 실행할 때 `data_seed/`가 여기로 복사되고, 이후 입력은 모두 `data/`에 쌓입니다.
- **클라우드 배포:** Streamlit Cloud처럼 재시작하면 파일이 지워지는 환경에서는 Google 시트 저장을 설정해야 합니다.

> ⚠️ `data_seed/`에는 사내 설비 목록, 판넬 구성, 점검 수치가 들어 있습니다. **저장소는 반드시 비공개(Private)로 만드세요.** 공개 저장소에 올려야 한다면 `data_seed/`를 빼고 올리세요.

## 폴더 구조

```
app.py            화면 (모바일 대응, 메뉴 7개)
agents.py         총괄 + 담당 4명 에이전트, 후속 질문 도구
briefing.py       이번 주 집계(JSON), 규칙 기반 요약
calc.py           공기압축기 가동률 계산 (엑셀 규칙 이식)
logbook.py        SPEC 판정, 입력 검증
ops.py            정비 예정 상태, 판넬 부하율
users.py          사번 로그인
config/users.csv  사번 목록 (수정 후 GitHub 재업로드)
storage.py        CSV / Google 시트 저장
prompts/agents/   총괄·담당자 프롬프트 (수정하면 다음 브리핑부터 반영)
data_seed/        초기 데이터
apps_script/      Google Apps Script 판 (운전일지·현황·압축기, 계정 없이 링크+PIN 입력)
실행.bat           Windows 실행 스크립트
```

## GitHub에 올리기

**GitHub Desktop을 쓸 때**
1. File → Add local repository로 이 폴더를 선택합니다. 저장소가 아니라고 나오면 "create a repository"를 누릅니다.
2. Publish repository를 누르고, **Keep this code private를 체크**합니다.

**명령줄을 쓸 때**

```bash
git init
git add .
git status        # .env, data/ 가 목록에 없는지 확인
git commit -m "Utility Agent 초기 버전"
git branch -M main
git remote add origin https://github.com/<계정>/utility-agent.git
git push -u origin main
```

## 계산 기준 요약

- **가동률:** 기간 부하시간 증가 ÷ 총 가동시간(부하 + 무부하) 증가
- **시간 자동 보정:** 구간 계산시간 = MAX(측정 경과시간, 총 가동시간 증가)
- **환산 사용시간**
  - 일반형: 부하 증가
  - 인버터형: 부하 증가 × 구간 로드율. 구간 로드율은 양 끝 운전 중 로드율의 평균입니다. 양 끝이 모두 정지 상태인데 부하가 늘었으면 최소/최대 운전값 비율로 추정합니다.
- **기간 용량 사용률:** Σ 환산 사용시간 ÷ Σ 구간 계산시간
- **이번 주:** 월요일 0시부터 현재까지 (한국 시간)
