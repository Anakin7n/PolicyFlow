@echo off
REM ============================================================
REM  PolicyFlow — one-click launcher
REM ============================================================
cd /d "%~dp0\.."
if exist ".venv\Scripts\activate.bat" (call .venv\Scripts\activate.bat) else (
    echo [ERROR] Virtual environment not found
    echo Run: python -m venv .venv ^&^& .venv\Scripts\activate ^&^& pip install -r requirements.txt
    pause & exit /b 1
)

REM Default: use YAML setting (no override)
if not defined ROUTING_MODE set ROUTING_MODE=

:menu
cls
echo.
echo   +------------------------------------------+
echo   ^|          PolicyFlow Launcher              ^|
echo   +------------------------------------------+
echo   ^|                                          ^|
echo   ^|  [1] Dashboard   Full TUI report         ^|
echo   ^|  [2] Serve       Start proxy             ^|
echo   ^|  [3] Classify    Test routing            ^|
if "%ROUTING_MODE%"=="" (set DISPLAY_MODE=default) else (set DISPLAY_MODE=%ROUTING_MODE%)
echo   ^|  [4] Mode: %DISPLAY_MODE%                            ^|
echo   ^|  [Q] Quit                                 ^|
echo   ^|                                          ^|
echo   +------------------------------------------+
echo.
choice /c 1234Q /n /m "  Select [1-4 or Q]: "
set CH=%errorlevel%

if %CH%==5 exit /b 0
if %CH%==4 goto :mode
if %CH%==3 goto :classify
if %CH%==2 goto :serve
if %CH%==1 goto :dashboard
exit /b 0

:dashboard
python -m policyflow report %*
goto :menu

:serve
if "%ROUTING_MODE%"=="" goto :serve_default
set "POLICYFLOW_ROUTING_MODE=%ROUTING_MODE%"
python -m policyflow serve --host 0.0.0.0 --port 8000 %*
goto :menu

:serve_default
python -m policyflow serve --host 0.0.0.0 --port 8000 %*
goto :menu

:classify
echo.
set /p PROMPT="  Enter prompt to test: "
if "%PROMPT%"=="" goto :menu
echo.
if "%ROUTING_MODE%"=="" goto :classify_default
set "POLICYFLOW_ROUTING_MODE=%ROUTING_MODE%"
python -m policyflow classify "%PROMPT%"
goto :classify_done

:classify_default
python -m policyflow classify "%PROMPT%"

:classify_done
echo.
pause
goto :menu

:mode
echo.
echo   +------------------------------------------+
echo   ^|  Routing Mode                             ^|
echo   +------------------------------------------+
echo   ^|                                          ^|
echo   ^|  [H] hybrid     Each policy decides       ^|
echo   ^|  [E] explicit   You pick every model      ^|
echo   ^|  [C] capability Algorithm picks best      ^|
echo   ^|  [N] default    Use YAML setting          ^|
echo   ^|                                          ^|
echo   +------------------------------------------+
echo.
choice /c HECN /n /m "  Choose mode [H, E, C, or N for YAML default]: "
set MC=%errorlevel%

if %MC%==1 set ROUTING_MODE=hybrid
if %MC%==2 set ROUTING_MODE=explicit
if %MC%==3 set ROUTING_MODE=capability
if %MC%==4 set ROUTING_MODE=

echo.
if "%ROUTING_MODE%"=="" (
    echo   Mode set to: YAML default
) else (
    echo   Mode set to: %ROUTING_MODE%
)
timeout /t 1 >nul
goto :menu
