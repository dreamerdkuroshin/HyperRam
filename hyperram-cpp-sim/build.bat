@echo off
echo ==========================================
echo HyperRAM C++ Kernel Simulator Build Script
echo ==========================================

:: Attempt to locate Visual Studio vcvarsall.bat
set "VCVARS=C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvarsall.bat"
if exist "%VCVARS%" goto :FOUND_VC
set "VCVARS=C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvarsall.bat"
if exist "%VCVARS%" goto :FOUND_VC
set "VCVARS=C:\Program Files\Microsoft Visual Studio\2022\Enterprise\VC\Auxiliary\Build\vcvarsall.bat"
if exist "%VCVARS%" goto :FOUND_VC
echo [ERROR] Visual Studio 2022 not found!
echo If you have g++ installed, run: g++ -o HyperRAM_Sim HyperRAM_Sim.cpp
pause & exit /b 1

:FOUND_VC
echo [INFO] Setting up C++ Build Environment...
call "%VCVARS%" x64 >nul

echo [INFO] Compiling HyperRAM_Sim.cpp...
cl /EHsc /std:c++17 HyperRAM_Sim.cpp

if %errorlevel% neq 0 (
    echo [ERROR] Compilation failed!
    pause
    exit /b %errorlevel%
)

echo [SUCCESS] Build complete! Running Simulator...
echo.
HyperRAM_Sim.exe
