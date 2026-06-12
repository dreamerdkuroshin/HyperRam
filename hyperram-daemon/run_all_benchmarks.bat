@echo off
setlocal enabledelayedexpansion

:: Check for Administrator privileges
net session >nul 2>&1
if %errorLevel% == 0 (
    echo Administrator privileges confirmed.
) else (
    echo [ERROR] This script must be run as Administrator!
    echo Please right-click and select "Run as Administrator".
    pause
    exit /b 1
)

set RESULTS_DIR=results\validation
if not exist "%RESULTS_DIR%" mkdir "%RESULTS_DIR%"

set LOG_FILE=%RESULTS_DIR%\validation_suite.log
echo HyperRAM Benchmark Suite > "%LOG_FILE%"

echo [*] Stopping existing HyperRAM driver...
sc.exe stop HyperRAM >> "%LOG_FILE%" 2>&1
timeout /t 2 /nobreak >nul

echo [*] Updating driver binary...
copy /y "..\hyperram-kernel-driver\HyperRAM.sys" "C:\Windows\System32\drivers\HyperRAM.sys" >> "%LOG_FILE%" 2>&1

echo [*] Starting HyperRAM driver...
sc.exe start HyperRAM >> "%LOG_FILE%" 2>&1
timeout /t 1 /nobreak >nul

echo [*] Running standard validation...
.\venv\Scripts\python.exe run_validation.py >> "%LOG_FILE%" 2>&1
echo   - Standard validation complete.

echo [*] Running recovery validation...
.\venv\Scripts\python.exe run_validation.py --recovery >> "%LOG_FILE%" 2>&1
echo   - Recovery validation complete.

echo [*] Running latency benchmark...
.\venv\Scripts\python.exe kernel_benchmark.py --pages 2000 --reads 10000 --cold >> "%LOG_FILE%" 2>&1
echo   - Latency benchmark complete.

echo.
echo All benchmarks completed successfully.
echo Output saved to: hyperram-daemon\%LOG_FILE%
echo.
pause
