@echo off
REM UAV-AI Windows Build Script
REM Builds the desktop application using PyInstaller

echo === UAV-AI Windows Build ===
echo.

REM Check Python is available
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python is not installed or not in PATH.
    pause
    exit /b 1
)

REM Install/upgrade PyInstaller
REM Install project dependencies (includes pyinstaller)
echo Installing project dependencies...
pip install -r requirements.txt

echo.
echo Building UAV-AI executable...
python -m PyInstaller uav-ai.spec --noconfirm

if errorlevel 1 (
    echo.
    echo ERROR: Build failed. Check the output above for details.
    pause
    exit /b 1
)

echo.
echo === Build complete! ===
echo Executable: dist\UAV-AI\UAV-AI.exe
echo.
echo To run: double-click dist\UAV-AI\UAV-AI.exe
pause
