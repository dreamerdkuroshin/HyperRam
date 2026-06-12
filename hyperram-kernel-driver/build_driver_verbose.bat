@echo on
echo ==========================================
echo  HyperRAM Kernel Driver - Build (WDM)
echo ==========================================

:: ---- Step 1: Locate MSVC ----
set "VCVARS=C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvarsall.bat"
if exist "%VCVARS%" goto :FOUND_VC
set "VCVARS=C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvarsall.bat"
if exist "%VCVARS%" goto :FOUND_VC
set "VCVARS=C:\Program Files\Microsoft Visual Studio\2022\Enterprise\VC\Auxiliary\Build\vcvarsall.bat"
if exist "%VCVARS%" goto :FOUND_VC
echo [ERROR] Visual Studio 2022 not found!
pause & exit /b 1

:FOUND_VC
call "%VCVARS%" x64 >nul
echo [INFO] MSVC environment ready.

:: ---- Step 2: Locate WDK ----
set "WDK_INC=C:\Program Files (x86)\Windows Kits\10\Include"
set "WDK_LIB=C:\Program Files (x86)\Windows Kits\10\Lib"
if not exist "%WDK_INC%" (
    echo [ERROR] WDK not found.
    pause & exit /b 1
)

:: Auto-detect WDK version
for /f "tokens=*" %%i in ('dir /b /ad "%WDK_INC%" ^| findstr /r "^10\."') do set "WDK_VER=%%i"
if "%WDK_VER%"=="" (
    echo [ERROR] Could not detect WDK version folder.
    pause & exit /b 1
)
echo [INFO] WDK Version: %WDK_VER%

:: ---- Step 3: Compile (Pure WDM - no WDF includes) ----
echo [INFO] Compiling Driver.cpp (pure WDM)...
cl.exe /c /kernel /Gz /W4 /WX- /Os /EHs-c- /GS- /Zl ^
    /I "%WDK_INC%\%WDK_VER%\km" ^
    /I "%WDK_INC%\%WDK_VER%\shared" ^
    /D_WIN64 /D_AMD64_ /DAMD64 ^
    /DNTDDI_VERSION=0x0A000000 /D_NTDDI_WIN10_ ^
    /DWINVER=0x0A00 /D_WIN32_WINNT=0x0A00 ^
    Driver.cpp

if %errorlevel% neq 0 (
    echo [ERROR] Compilation failed!
    pause & exit /b 1
)
echo [INFO] Compilation OK.

:: ---- Step 4: Link (WDM-only libs, correct entry point) ----
echo [INFO] Linking driver...
link.exe ^
    /DRIVER ^
    /SUBSYSTEM:NATIVE,10.00 ^
    /NODEFAULTLIB ^
    /ENTRY:DriverEntry ^
    /GUARD:CF ^
    /LIBPATH:"%WDK_LIB%\%WDK_VER%\km\x64" ^
    /OUT:HyperRAM.sys ^
    Driver.obj ^
    ntoskrnl.lib ^
    wdm.lib ^
    ntstrsafe.lib ^
    BufferOverflowK.lib

if %errorlevel% neq 0 (
    echo [ERROR] Linking failed!
    exit /b 1
)

echo.
echo [SUCCESS] HyperRAM.sys built (pure WDM, no KMDF dependency).
echo.
echo NEXT STEPS:
echo   Run as Admin:  .\install_and_start.ps1
