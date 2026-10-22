@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Utility 운전일지 - Streamlit 테스트 서버
set PYTHONUTF8=1

set PY=python
%PY% --version >nul 2>&1 || set PY=py
%PY% --version >nul 2>&1 || (
  echo.
  echo [오류] Python이 설치되어 있지 않습니다.
  echo https://www.python.org/downloads/ 에서 설치하세요. 설치 시 "Add python.exe to PATH" 체크
  pause
  exit /b 1
)

echo [1/2] 필요한 패키지 설치 중... (처음 한 번만 1~2분)
%PY% -m pip install -r requirements.txt --disable-pip-version-check -q
if errorlevel 1 (
  echo [오류] 패키지 설치 실패. 인터넷 연결 또는 회사 프록시를 확인하세요.
  pause
  exit /b 1
)

rem ---- 사용 중이 아닌 포트 찾기 (8765부터) ----
set PORT=
for %%p in (8765 8766 8767 8768 8769 8770 8780 8790) do (
  if not defined PORT (
    netstat -ano | findstr /r /c:":%%p .*LISTENING" >nul || set PORT=%%p
  )
)
if not defined PORT set PORT=8799

echo.
echo [2/2] 서버 시작 (포트 %PORT%)
echo   PC 브라우저:  http://localhost:%PORT%
echo   휴대폰(같은 와이파이):
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4"') do for /f "tokens=*" %%b in ("%%a") do echo     http://%%b:%PORT%
echo   로그인: 사번 (config\users.csv)     종료: 이 창을 닫거나 Ctrl+C
echo.
%PY% -m streamlit run app.py --server.address 0.0.0.0 --server.port %PORT% --server.headless false
pause
