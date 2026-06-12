@echo off
set "VCVARS=C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvarsall.bat"
if exist "%VCVARS%" goto :FOUND_VC
set "VCVARS=C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvarsall.bat"
if exist "%VCVARS%" goto :FOUND_VC
set "VCVARS=C:\Program Files\Microsoft Visual Studio\2022\Enterprise\VC\Auxiliary\Build\vcvarsall.bat"
if exist "%VCVARS%" goto :FOUND_VC
echo [ERROR] Visual Studio 2022 not found!
exit /b 1

:FOUND_VC
call "%VCVARS%" x64 >nul
echo [INFO] Compiling stress_test.cpp...
cl.exe /EHsc /O2 "%~dp0stress_test.cpp" /Fe"%~dp0stress_test.exe"
if %errorlevel% neq 0 (
    echo [ERROR] Compilation failed!
    exit /b 1
)
echo [SUCCESS] stress_test.exe built successfully.
