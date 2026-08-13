@echo off
REM chcp 65001
echo ========================================
echo   RENUP - Build va Release
echo ========================================
echo.

REM ========================================
REM Kiem tra tools bat buoc (Python, PyInstaller)
REM Neu thieu thi build khong the tiep tuc, dung ngay
REM ========================================
python --version >nul 2>&1
if errorlevel 1 (
    echo [LOI] Khong tim thay Python. Build khong the tiep tuc.
    pause
    exit /b 1
)
pip show pyinstaller >nul 2>&1
if errorlevel 1 (
    echo [INFO] Dang cai PyInstaller...
    pip install pyinstaller
)

REM ========================================
REM Kiem tra tools tuy chon (Git, GitHub CLI)
REM Chi can cho buoc commit/push/release o cuoi script.
REM Thieu cac tool nay KHONG duoc lam hong buoc build .exe -
REM chi bo qua buoc phat hanh va bao ro cho nguoi dung.
REM ========================================
set "HAS_GIT=1"
git --version >nul 2>&1
if errorlevel 1 (
    set "HAS_GIT=0"
    echo [CANH BAO] Khong tim thay git. Se bo qua buoc commit/push.
)
set "HAS_GH=1"
gh --version >nul 2>&1
if errorlevel 1 (
    set "HAS_GH=0"
    echo [CANH BAO] Khong tim thay GitHub CLI - gh. Se bo qua buoc tao release.
    echo [CANH BAO] Cai tai: https://cli.github.com neu muon phat hanh tu dong.
)
echo.

REM ========================================
REM 1. Doc version hien tai
REM ========================================
set /p CURRENT_VER=<version.txt
for /f "delims=" %%a in ('python -c "print('%CURRENT_VER%'.strip())"') do set CURRENT_VER=%%a
echo [INFO] Version hien tai: v%CURRENT_VER%

REM ========================================
REM 2. Bump version (patch: x.y.Z+1)
REM Ghi ngay vao version.txt vi PyInstaller can embed dung noi dung
REM nay luc build (--add-data "version.txt;."), nen file phai dung
REM TRUOC khi build chay. Neu build that bai o buoc 4, version.txt se
REM duoc KHOI PHUC ve lai CURRENT_VER (xem buoc 4 ben duoi) - tranh
REM "dot" mat so version cho nhung lan build hong (da tung mat lien
REM tiep 1.1.22 -> 1.1.25 vi khong co co che nay).
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
set "LINE=__EOF_SENTINEL__"
set /p "LINE=  > "
if "%LINE%"=="__EOF_SENTINEL__" (
    echo [CANH BAO] Stdin ket thuc som ^(khong doc duoc dong nhap moi^). Dung lai voi changelog da nhap duoc.
    goto changelog_done
)
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
REM Thu muc release la thu muc anh em "RENUP" nam canh thu muc build
REM (RENUP_build). Dung duong dan tuong doi qua %~dp0 thay vi hardcode
REM ten o dia - du an da doi o dia mot lan (G: -> E:) va lam vo script
REM khi hardcode ten o dia. %%~fI chuan hoa ket qua thanh duong dan
REM tuyet doi, gon gang (khong con doan "..\" trong duong dan).
for %%I in ("%~dp0..\RENUP") do set "RELEASE_DIR=%%~fI"
if not exist "%RELEASE_DIR%" mkdir "%RELEASE_DIR%"

echo [BUILD] Dang build RENUP.exe...
echo [INFO] Release dir: %RELEASE_DIR%
echo.
REM Ghi chu ve --additional-hooks-dir ".":
REM Tro toi hook-customtkinter.py (tan du tu thoi Tkinter, xem CLAUDE.md).
REM Hook nay CHI duoc PyInstaller thuc thi khi "customtkinter" thuc su
REM nam trong dependency graph cua RENUP_gui.py. Da kiem tra: khong co
REM file nao trong du an import customtkinter, nen hook khong chay va
REM KHONG lam hong build du package chua duoc cai. Giu nguyen co nay
REM (khong doi hanh vi build). De nghi rieng: xoa hook-customtkinter.py
REM trong mot dot don dep rieng - ngoai pham vi sua build.bat.
REM
REM Ghi chu ve --paths: da BO co --paths tro toi
REM "%APPDATA%\Python\Python39\site-packages" (duong dan nay khong ton tai
REM tren may hien tai; may dang dung Python 3.13 cai tai
REM C:\Users\...\Python313, khong dung scheme "pip install --user" cu).
REM PyInstaller tu dung sys.path cua chinh interpreter "python" dang chay
REM script nay de tim package (pywebview, PIL, v.v.) nen khong can hardcode
REM duong dan site-packages theo tung phien ban Python - tranh vo script
REM lan nua khi nang cap Python trong tuong lai.
pyinstaller --onefile --windowed --name "RENUP" --distpath "%RELEASE_DIR%" --add-data "version.txt;." --add-data "icon.ico;." --add-data "ui;ui" --icon "icon.ico" --additional-hooks-dir "." --hidden-import PIL RENUP_gui.py
if not exist "%RELEASE_DIR%\RENUP.exe" (
    echo %CURRENT_VER%> version.txt
    echo [LOI] Build that bai! Da khoi phuc version.txt ve v%CURRENT_VER% ^(version moi v%NEW_VER% CHUA duoc su dung^).
    pause
    exit /b 1
)
echo.
echo [OK] Build thanh cong: %RELEASE_DIR%\RENUP.exe
echo.

REM Copy files can thiet vao release folder
xcopy /y /e /i "bin\codes" "%RELEASE_DIR%\bin\codes" >nul
copy /y "bin\ffmpeg.exe" "%RELEASE_DIR%\bin\ffmpeg.exe" >nul
copy /y "bin\ffprobe.exe" "%RELEASE_DIR%\bin\ffprobe.exe" >nul
copy /y "bin\yt-dlp.exe" "%RELEASE_DIR%\bin\yt-dlp.exe" >nul

REM Don dep
if exist "RENUP.spec" del "RENUP.spec"
if exist "build" rmdir /s /q "build"
if exist "__pycache__" rmdir /s /q "__pycache__"

REM ========================================
REM 5. Git commit and push (chi khi co git)
REM Day dung NHANH HIEN TAI dang checkout (khong hardcode "main" -
REM script co the chay tu bat ky nhanh nao), va DUNG toan bo script
REM neu commit/push that bai thuc su - khong duoc in [OK] khi khong
REM co gi duoc luu that (bug cu: loi "dubious ownership" bi nuot,
REM script van bao push thanh cong).
REM
REM Logic commit/push nam trong subroutine :git_commit_push (cuoi
REM file) de tranh bay "delayed expansion" trong batch:
REM   - Goi bang CALL nen moi dong lenh trong subroutine duoc parse
REM     va thuc thi RIENG LE, khong bi "dong bang" gia tri bien tai
REM     thoi diem parse toan bo khoi ( ... ) - day chinh la loi neu
REM     doc lai (dung %VAR%) mot bien vua duoc SET trong CUNG mot
REM     khoi ( ... ) parenthesized.
REM   - Kiem tra loi bang "if errorlevel N" (dang dac biet cua batch,
REM     danh gia TAI THOI DIEM CHAY, khac voi "if %errorlevel% NEQ 0"
REM     von bi substitute tai THOI DIEM PARSE) nen khong can bat
REM     setlocal enabledelayedexpansion cho ca script - tranh luon mot
REM     bay khac: dau "!" don le trong cac dong echo hien co (vd
REM     "Build that bai!") se bi delayed expansion nuot mat neu bat no
REM     toan cuc.
REM ========================================
if "%HAS_GIT%"=="1" (
    set "CUR_BRANCH="
    for /f "delims=" %%b in ('git rev-parse --abbrev-ref HEAD 2^>nul') do set "CUR_BRANCH=%%b"
    if "%CUR_BRANCH%"=="" (
        echo [CANH BAO] Khong xac dinh duoc nhanh git hien tai ^(co the day khong phai git repo^). Bo qua buoc commit/push.
    ) else if /i "%CUR_BRANCH%"=="HEAD" (
        echo [CANH BAO] Dang o trang thai detached HEAD ^(khong nam tren nhanh nao^). Bo qua buoc commit/push de tranh day nham vao nhanh sai.
    ) else (
        call :git_commit_push "%CUR_BRANCH%"
        if errorlevel 1 (
            echo [LOI] Buoc commit/push that bai - xem chi tiet loi git o tren. Dung script.
            echo [LOI] File RENUP.exe da build xong tai "%RELEASE_DIR%" nhung CHUA duoc dua len GitHub.
            pause
            exit /b 1
        )
    )
) else (
    echo [BO QUA] Khong co git, bo qua buoc commit/push.
)
echo.

REM ========================================
REM 6. Tao GitHub Release (chi khi co gh)
REM ========================================
if "%HAS_GH%"=="1" (
    echo [RELEASE] Dang tao release v%NEW_VER%...

    REM Tao release notes
    echo ## RENUP v%NEW_VER%> release_notes.tmp
    echo.>> release_notes.tmp
    echo ### Thay doi>> release_notes.tmp
    type changelog.tmp >> release_notes.tmp

    gh release create "v%NEW_VER%" "%RELEASE_DIR%\RENUP.exe" --title "RENUP v%NEW_VER%" --notes-file release_notes.tmp
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

    del release_notes.tmp 2>nul
) else (
    echo.
    echo ========================================
    echo   [OK] BUILD THANH CONG!
    echo   Version: v%NEW_VER%
    echo   File: %RELEASE_DIR%\RENUP.exe
    echo   [BO QUA] Khong tao GitHub Release - thieu gh.
    echo ========================================
)

REM Don dep
del changelog.tmp 2>nul

echo.
pause
exit /b 0

REM ========================================
REM Subroutine: git_commit_push
REM %1 = ten nhanh can push
REM Tra ve (qua "exit /b"): 0 = OK, 1 = that bai (commit that bai
REM THAT SU, hoac push that bai).
REM
REM "Khong co gi de commit" KHONG duoc tinh la loi. Dieu nay duoc phat
REM hien TRUOC bang "git diff --cached --quiet" (kiem tra co thay doi
REM da staged hay khong: errorlevel 1 = co khac biet = co thay doi),
REM chu KHONG dua vao exit code cua chinh lenh "git commit" - exit code
REM cua "git commit" tra ve khac 0 CA HAI truong hop "khong co gi de
REM commit" LAN "loi commit that su", nen khong the phan biet duoc neu
REM chi doc thang no.
REM ========================================
:git_commit_push
set "BRANCH_NAME=%~1"
echo [GIT] Commit v%NEW_VER% tren nhanh "%BRANCH_NAME%"...
git add -A
git diff --cached --quiet
if errorlevel 1 (
    git commit -m "release: v%NEW_VER%"
    if errorlevel 1 (
        echo [LOI] git commit that bai.
        exit /b 1
    )
) else (
    echo [INFO] Khong co thay doi nao de commit, tiep tuc push nhanh hien co.
)
git push origin "%BRANCH_NAME%"
if errorlevel 1 (
    echo [LOI] git push that bai ^(kiem tra ket noi mang, quyen truy cap repo, xung dot voi remote, hoac loi "dubious ownership"^).
    exit /b 1
)
echo [OK] Da push len GitHub ^(nhanh: %BRANCH_NAME%^).
exit /b 0
