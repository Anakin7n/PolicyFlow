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

:menu
cls
echo.
echo   +------------------------------------+
echo   ^|        PolicyFlow Launcher          ^|
echo   +------------------------------------+
echo   ^|                                    ^|
echo   ^|  [1] Dashboard   Full TUI report   ^|
echo   ^|  [2] Serve       Start proxy       ^|
echo   ^|  [3] Classify    Test routing     ^|
echo   ^|  [Q] Quit                           ^|
echo   ^|                                    ^|
echo   +------------------------------------+
echo.
choice /c 123Q /n /m "  Select [1, 2, 3, or Q]: "
set CH=%errorlevel%

if %CH%==4 exit /b 0
if %CH%==3 goto :classify
if %CH%==2 goto :serve
if %CH%==1 goto :dashboard
exit /b 0

:dashboard
python -m policyflow report %*
goto :menu

:serve
python -m policyflow serve --host 0.0.0.0 --port 8000 %*
goto :menu

:classify
echo.
set /p PROMPT="  Enter prompt to test: "
if "%PROMPT%"=="" goto :menu
echo.
python -m policyflow classify "%PROMPT%"
echo.
pause
goto :menu
