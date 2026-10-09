@echo off
rem ==============================================================================
rem start_peak_miner.bat — Launch PeakMiner on Windows connected to miner.py
rem ==============================================================================
setlocal enabledelayedexpansion

cd /d "%~dp0"

set "PEAK_BIN="
if exist "%~dp0peakminer.exe" (
    set "PEAK_BIN=%~dp0peakminer.exe"
) else if exist "%~dp0peakminer\peakminer.exe" (
    set "PEAK_BIN=%~dp0peakminer\peakminer.exe"
) else (
    echo [!] peakminer.exe not found. Please ensure peakminer\peakminer.exe exists.
    pause
    exit /b 1
)

if "%PEAK_WALLET%"=="" set "PEAK_WALLET=krxYRPV4WQ"
if "%PEAK_WORKER%"=="" set "PEAK_WORKER=peak-win-gpu"
if "%PEAK_POOL%"=="" set "PEAK_POOL=stratum+tcp://127.0.0.1:3333"
if "%PEAK_PASS%"=="" set "PEAK_PASS=x"

echo ====================================================
echo           PEAKMINER - PEARL (PRL) OFFLINE           
echo ====================================================
echo Binary:  %PEAK_BIN%
echo Pool:    %PEAK_POOL% (Local miner.py Stratum Bridge)
echo Wallet:  %PEAK_WALLET%.%PEAK_WORKER%
echo Coin:    pearl
echo ====================================================
echo.

"%PEAK_BIN%" -o "%PEAK_POOL%" -u "%PEAK_WALLET%.%PEAK_WORKER%" -p "%PEAK_PASS%" -c pearl %*

echo.
echo === PeakMiner Stopped ===
pause
