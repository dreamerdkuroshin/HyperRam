@echo off
echo ==========================================
echo  Building HyperRAM AI Loader
echo ==========================================

:: Locate MSVC
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
echo [INFO] MSVC environment ready.

echo [INFO] Compiling AILoader.cpp...
cl.exe /EHsc /O2 /W4 /WX- /MD /std:c++17 AILoader.cpp /link User32.lib Kernel32.lib Ws2_32.lib

if %errorlevel% neq 0 (
    echo [ERROR] Compilation failed!
    exit /b 1
)

echo [SUCCESS] AILoader.exe built successfully.
echo.
echo You can now run: .\AILoader.exe
