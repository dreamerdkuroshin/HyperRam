# PowerShell script to build and run the stress test
# Requires Visual Studio Developer Command Prompt or an installed MSVC compiler.

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$sourceFile = Join-Path $scriptDir "stress_test.cpp"
$outputFile = Join-Path $scriptDir "stress_test.exe"

# Try to compile
Write-Host "Building stress_test.cpp..." -ForegroundColor Cyan

# Check if cl.exe is in path
if (Get-Command cl.exe -ErrorAction SilentlyContinue) {
    cl.exe /EHsc /O2 $sourceFile /Fe"$outputFile"
    if ($LASTEXITCODE -eq 0) {
        Write-Host "Build successful." -ForegroundColor Green
        Write-Host "Starting Stress Test (run as Administrator if required)..." -ForegroundColor Yellow
        & $outputFile
    } else {
        Write-Host "Build failed." -ForegroundColor Red
    }
} else {
    # Try g++ if MSVC is not available
    if (Get-Command g++ -ErrorAction SilentlyContinue) {
        g++ -O3 -std=c++11 -o $outputFile $sourceFile
        if ($LASTEXITCODE -eq 0) {
            Write-Host "Build successful." -ForegroundColor Green
            Write-Host "Starting Stress Test (run as Administrator if required)..." -ForegroundColor Yellow
            & $outputFile
        } else {
            Write-Host "Build failed." -ForegroundColor Red
        }
    } else {
        Write-Host "Could not find 'cl.exe' or 'g++'. Please run this script from a Visual Studio Developer Command Prompt or install a C++ compiler." -ForegroundColor Red
    }
}
