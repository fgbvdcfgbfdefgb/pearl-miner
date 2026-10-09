@echo off
rem ==============================================================================
rem start_forge_miner.bat — Launch ForgeMiner on Windows connected to miner.py
rem ==============================================================================
setlocal enabledelayedexpansion

cd /d "%~dp0"

set "FORGE_BIN="
if exist "%~dp0forge.exe" (
    set "FORGE_BIN=%~dp0forge.exe"
) else if exist "%~dp0forgeminer\forge.exe" (
    set "FORGE_BIN=%~dp0forgeminer\forge.exe"
) else (
    echo [!] forge.exe not found. Please ensure forgeminer\forge.exe exists.
    pause
    exit /b 1
)

if "%FORGE_WALLET%"=="" set "FORGE_WALLET=krxYRPV4WQ"
if "%FORGE_WORKER%"=="" set "FORGE_WORKER=forge-win-gpu"
if "%FORGE_POOL%"=="" set "FORGE_POOL=127.0.0.1:3333"

echo ====================================================
echo           FORGEMINER - PEARL (PRL) OFFLINE          
echo ====================================================
echo Binary:  %FORGE_BIN%
echo Pool:    %FORGE_POOL% (Local miner.py Stratum Bridge)
echo Wallet:  %FORGE_WALLET%
echo Worker:  %FORGE_WORKER%
echo ====================================================
echo.

"%FORGE_BIN%" --algorithm pearlhash --pool "%FORGE_POOL%" --wallet "%FORGE_WALLET%" --worker "%FORGE_WORKER%" --tls false %*

echo.
echo === ForgeMiner Stopped ===
pause
