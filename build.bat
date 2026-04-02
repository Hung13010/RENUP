@echo off
REM chcp 65001
echo ========================================
echo   RENUP - Build va Release
echo ========================================
echo.

REM Kiem tra tools
python --version >nul 2>&1
if errorlevel 1 (
    echo [LOI] Khong tim thay Python.
    pause
    exit /b 1
)
gh --version >nul 2>&1
if errorlevel 1 (
    echo [LOI] Khong tim thay GitHub CLI. Cai tai: https://cli.github.com
    pause
    exit /b 1
)
pip show pyinstaller >nul 2>&1
if errorlevel 1 (
    echo Dang cai PyInstaller...
    pip install pyinstaller
)

REM ========================================
REM 1. Doc version hien tai
REM ========================================
set /p CURRENT_VER=<version.txt
for /f "delims=" %%a in ('python -c "print('%CURRENT_VER%'.strip())"') do set CURRENT_VER=%%a
echo [INFO] Version hien tai: v%CURRENT_VER%

REM ========================================
REM 2. Bump version (patch: x.y.Z+1)
REM ========================================
for /f "delims=" %%a in ('python -c "v='%CURRENT_VER%'.split('.'); v[-1]=str(int(v[-1])+1); print('.'.join(v))"') do set NEW_VER=%%a
echo %NEW_VER%> version.txt
echo [INFO] Version moi: v%NEW_VER%
echo.

REM ========================================
REM 3. Nhap changelog
REM ========================================
echo Nhap changelog (moi dong 1 thay doi, go DONE de ket thuc):
echo.
if exist changelog.tmp del changelog.tmp

:changelog_loop
set "LINE="
set /p "LINE=  > "
if "%LINE%"=="" goto changelog_loop
if /i "%LINE%"=="DONE" goto changelog_done
echo - %LINE%>> changelog.tmp
goto changelog_loop

:changelog_done
echo.
if not exist changelog.tmp (
    echo - Bug fixes and improvements> changelog.tmp
)

REM ========================================
REM 4. Build RENUP.exe
REM ========================================
echo [BUILD] Dang build RENUP.exe...
echo.
pyinstaller --onefile --windowed --name "RENUP" --distpath . --add-data "version.txt;." --icon "icon.ico" --hidden-import customtkinter --hidden-import darkdetect --collect-all customtkinter RENUP_gui.py
if not exist "RENUP.exe" (
    echo [LOI] Build that bai!
    pause
    exit /b 1
)
echo.
echo [OK] Build thanh cong: RENUP.exe
echo.

REM Don dep
if exist "RENUP.spec" del "RENUP.spec"
if exist "build" rmdir /s /q "build"
if exist "__pycache__" rmdir /s /q "__pycache__"

REM ========================================
REM 5. Git commit and push
REM ========================================
echo [GIT] Commit v%NEW_VER%...
git add -A
git commit -m "release: v%NEW_VER%"
git push origin main
echo [OK] Da push len GitHub.
echo.

REM ========================================
REM 6. Tao GitHub Release
REM ========================================
echo [RELEASE] Dang tao release v%NEW_VER%...

REM Tao release notes
echo ## RENUP v%NEW_VER%> release_notes.tmp
echo.>> release_notes.tmp
echo ### Thay doi>> release_notes.tmp
type changelog.tmp >> release_notes.tmp

gh release create "v%NEW_VER%" RENUP.exe --title "RENUP v%NEW_VER%" --notes-file release_notes.tmp
if errorlevel 1 (
    echo [LOI] Tao release that bai!
    del changelog.tmp release_notes.tmp 2>nul
    pause
    exit /b 1
)

echo.
echo ========================================
echo   [OK] RELEASE THANH CONG!
echo   Version: v%NEW_VER%
echo   https://github.com/Hung13010/RENUP/releases/tag/v%NEW_VER%
echo ========================================

REM Don dep
del changelog.tmp release_notes.tmp 2>nul

echo.
pause
