@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo =============================================
echo   Game Window Synchronizer
echo   ^<+LifeAfter^> ^| License Protected
echo =============================================
echo.
echo Starting sync manager...
echo.
python game_sync.py
pause
