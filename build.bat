@echo off
echo ========================================
echo   Build RENUP GUI -> .exe
echo ========================================
echo.

REM Kiem tra Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [LOI] Khong tim thay Python. Vui long cai Python 3.x
    echo      https://www.python.org/downloads/
    pause
    exit /b 1
)

REM Cai PyInstaller neu chua co
pip show pyinstaller >nul 2>&1
if errorlevel 1 (
    echo Dang cai PyInstaller...
    pip install pyinstaller
)

echo.
echo Dang build...
echo.

pyinstaller --onefile --windowed --name "RENUP" --distpath . RENUP_gui.py

echo.
if exist "RENUP.exe" (
    echo [OK] Build thanh cong! File: RENUP.exe
) else (
    echo [LOI] Build that bai. Xem thong bao loi ben tren.
)

REM Don dep file build tam
if exist "RENUP.spec" del "RENUP.spec"
if exist "build" rmdir /s /q "build"
if exist "__pycache__" rmdir /s /q "__pycache__"

echo.
pause
