@echo off
REM ============================================================
REM  reproduce.bat  —  Regenerate all HyperRAM paper figures
REM  from the latest raw CSV outputs in results\
REM
REM  Usage:
REM    reproduce.bat                  # latest run, all figures
REM    reproduce.bat --aggregate      # mean+/-std across ALL runs
REM    reproduce.bat --run-ts 20260608_013903  # specific run
REM ============================================================
setlocal

set SCRIPT_DIR=%~dp0hyperram-daemon
set PYTHON=%SCRIPT_DIR%\venv\Scripts\python.exe
set PLOT=%SCRIPT_DIR%\plot_results.py
set RESULTS=%~dp0results

echo.
echo  HyperRAM Figure Reproducer
echo  Python : %PYTHON%
echo  Results: %RESULTS%
echo  Args   : %*
echo.

if not exist "%PYTHON%" (
    echo  [ERROR] venv not found. Run:
    echo    cd hyperram-daemon
    echo    python -m venv venv
    echo    venv\Scripts\pip install matplotlib numpy
    exit /b 1
)

"%PYTHON%" "%PLOT%" --results-dir "%RESULTS%" %*

if %ERRORLEVEL% neq 0 (
    echo.
    echo  [ERROR] plot_results.py exited with code %ERRORLEVEL%
    exit /b %ERRORLEVEL%
)

echo.
echo  Generating Fig 6 (architectural split / most important result)...
"%PYTHON%" "%SCRIPT_DIR%\fig_architectural_comparison.py" --output-dir "%RESULTS%\figures"

if %ERRORLEVEL% neq 0 (
    echo  [WARN] fig_architectural_comparison.py failed - check Python deps
)

echo.
echo  Done. Figures saved to: %RESULTS%\figures\
echo  To embed in LaTeX:  \includegraphics{figures/figN_...png}
