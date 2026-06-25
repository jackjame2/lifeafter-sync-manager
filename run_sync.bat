@echo off
chcp 65001 >nul
cd /d "%~dp0"

:: Check if KeymouseGo exists
if not exist "KeymouseGo_v5_2_1-win.exe" (
    echo =============================================
    echo   KeymouseGo not found!
    echo   Downloading from official source...
    echo =============================================
    echo.
    powershell -ExecutionPolicy Bypass -File "%~dp0download_keymousego.ps1"
    if errorlevel 1 (
        echo.
        echo Failed to download KeymouseGo.
        echo Please download manually from:
        echo   https://github.com/taojy123/KeymouseGo/releases
        echo.
        pause
        exit /b 1
    )
)

echo =============================================
echo   Game Window Synchronizer
echo   ^<+LifeAfter^> ^| License Protected
echo =============================================
echo.
echo Starting sync manager...
echo.
python game_sync.py
pause
