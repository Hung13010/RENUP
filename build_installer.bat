@echo off
REM ============================================================================
REM  RENUP - Build va dong goi BO CAI DAT (PyInstaller --onedir + Inno Setup)
REM
REM  Script nay TACH RIENG khoi build.bat (luong --onefile + GitHub Release
REM  cu, van giu nguyen va van chay duoc). Ly do tach thanh file rieng thay
REM  vi sua de len build.bat: build.bat vua duoc sua nhieu loi va dang chay
REM  on dinh; gop chung hai luong dong goi khac nhau (--onefile vs --onedir)
REM  vao mot file se rat kho lan loi khi co su co xay ra sau nay.
REM
REM  VI SAO --onedir thay vi --onefile: ban --onefile hien tai (build.bat)
REM  tu giai nen "ruot" ra thu muc tam %TEMP%\_MEIxxxxx MOI LAN CHAY, roi
REM  nap python313.dll tu do. Nguoi dung tung gap loi:
REM      Failed to load Python DLL '...\_MEI108122\python313.dll'.
REM      LoadLibrary: The specified module could not be found.
REM  Da kiem chung bang pyi-archive_viewer: python313.dll CO NAM TRONG goi
REM  (6.1 MB) - tuc day la loi o KHAU GIAI NEN LUC CHAY, khong phai thieu
REM  file. --onedir bo hoan toan khau giai nen tam thoi nay: app chay thang
REM  tu thu muc da cai, khong con "_MEI..." nao ca.
REM
REM  Ket qua cua script nay la MOT FILE CAI DAT (.exe cua Inno Setup), KHONG
REM  phai file RENUP.exe chay truc tiep nhu build.bat. Nguoi dung tai file
REM  cai dat nay ve, chay no, no se cai RENUP vao thu muc rieng cua user
REM  (khong can quyen admin) roi tao shortcut Start Menu/Desktop.
REM ============================================================================
echo ========================================
echo   RENUP - Build BO CAI DAT (--onedir)
echo ========================================
echo.

REM ========================================
REM Kiem tra tools bat buoc: Python, PyInstaller, Inno Setup (ISCC.exe)
REM Thieu bat ky cai nao trong 3 cai nay thi KHONG the tao ra bo cai dat -
REM dung ngay, khac voi git/gh o duoi (chi anh huong buoc phat hanh cuoi).
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

REM ----------------------------------------------------------------------
REM Tim ISCC.exe (trinh bien dich Inno Setup) ma KHONG hardcode ky tu o dia
REM hay ten tai khoan Windows. Truoc day dong nay go thang
REM "C:\Users\Admin\AppData\Local\Programs\InnoSetup6\ISCC.exe" - chi dung
REM tren dung mot may, dung mot user. Cung mot loai loi voi viec tung
REM hardcode o dia du an (xem ghi chu %~dp0 trong build.bat).
REM Thu tu uu tien, dung ngay khi tim thay:
REM   1. Bien moi truong ISCC_PATH  (ghi de thu cong, cao nhat)
REM   2. ISCC.exe co san trong PATH (%%~$PATH:P tra ve rong neu khong thay)
REM   3. Cac vi tri cai dat chuan, tra qua bien moi truong cua Windows
REM      (%LOCALAPPDATA%, %ProgramFiles(x86)%, %ProgramFiles%) - tu dong
REM      dung o dia he thong that su, du no khong phai C:.
REM ----------------------------------------------------------------------
if not defined ISCC_PATH for %%P in (ISCC.exe) do if not "%%~$PATH:P"=="" set "ISCC_PATH=%%~$PATH:P"
if not defined ISCC_PATH if exist "%LOCALAPPDATA%\Programs\InnoSetup6\ISCC.exe" set "ISCC_PATH=%LOCALAPPDATA%\Programs\InnoSetup6\ISCC.exe"
if not defined ISCC_PATH if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC_PATH=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not defined ISCC_PATH if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC_PATH=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not defined ISCC_PATH (
    echo [LOI] Khong tim thay ISCC.exe o bat ky vi tri quen thuoc nao.
    echo [LOI] Cai Inno Setup 6 ^(https://jrsoftware.org/isinfo.php^) hoac dat
    echo [LOI] bien moi truong ISCC_PATH tro dung noi cai dat truoc khi chay lai.
    pause
    exit /b 1
)
if not exist "%ISCC_PATH%" (
    echo [LOI] Khong tim thay ISCC.exe tai "%ISCC_PATH%".
    echo [LOI] Kiem tra lai bien moi truong ISCC_PATH.
    pause
    exit /b 1
)
echo [OK] Tim thay Inno Setup: %ISCC_PATH%

REM ========================================
REM Kiem tra tools tuy chon (Git, GitHub CLI) - chi can cho buoc commit/
REM push/release o cuoi script, giong het build.bat.
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
REM Ghi ngay vao version.txt vi PyInstaller can embed dung noi dung nay luc
REM build (--add-data "version.txt;."), va Inno Setup (RENUP.iss) cung doc
REM truc tiep tu version.txt de dat AppVersion + ten file cai dat. Neu bat
REM ky buoc nao ben duoi that bai, version.txt duoc KHOI PHUC ve CURRENT_VER
REM (xem cac khoi "if errorlevel" ben duoi) - tranh dot mat so version cho
REM nhung lan build hong, dung nhu bai hoc tu build.bat (#018).
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
REM 4. Build RENUP bang PyInstaller --onedir
REM ========================================
REM Thu muc dist RIENG cho luong installer (dist_installer\RENUP), KHONG
REM dung chung voi RELEASE_DIR cua build.bat (thu muc anh em "..\RENUP")
REM de hai luong dong goi --onefile / --onedir khong bao gio dam vao nhau.
for %%I in ("%~dp0dist_installer") do set "DIST_DIR=%%~fI"
set "APP_STAGE=%DIST_DIR%\RENUP"
for %%I in ("%~dp0build_installer_tmp") do set "WORK_DIR=%%~fI"

REM Ap dung dung bai hoc cua loi #024 (build.bat) ngay tu dau cho luong
REM --onedir nay: XOA ban build cu TRUOC khi goi PyInstaller. Neu khong
REM xoa, mot lan build hong co the de sot RENUP.exe cu trong APP_STAGE,
REM khien buoc kiem tra "exist" ben duoi lam tuong build moi da thanh
REM cong roi dong goi nham ban cu vao installer.
if exist "%APP_STAGE%" rmdir /s /q "%APP_STAGE%"

echo [BUILD] Dang build RENUP ^(--onedir^)...
echo [INFO] Thu muc stage: %APP_STAGE%
echo.
REM Cung mot bo co --add-data / --hidden-import / --additional-hooks-dir
REM nhu build.bat (xem ghi chu chi tiet trong build.bat ve tung co) - chi
REM khac --onedir thay vi --onefile va --distpath/--workpath rieng de
REM khong dung chung thu muc trung gian voi build.bat.
pyinstaller --onedir --windowed --name "RENUP" --distpath "%DIST_DIR%" --workpath "%WORK_DIR%" --add-data "version.txt;." --add-data "icon.ico;." --add-data "ui;ui" --icon "icon.ico" --additional-hooks-dir "." --hidden-import PIL RENUP_gui.py

REM Hai lop kiem tra bo sung cho nhau, giong het build.bat sau khi sua
REM loi #024: (1) ma loi cua chinh PyInstaller, (2) file .exe co thuc su
REM ton tai sau khi chay xong.
if errorlevel 1 (
    echo %CURRENT_VER%> version.txt
    echo [LOI] PyInstaller bao loi ^(exit code != 0^). Da khoi phuc version.txt ve v%CURRENT_VER% ^(version moi v%NEW_VER% CHUA duoc su dung^).
    del changelog.tmp 2>nul
    pause
    exit /b 1
)
if not exist "%APP_STAGE%\RENUP.exe" (
    echo %CURRENT_VER%> version.txt
    echo [LOI] Build that bai! Khong thay "%APP_STAGE%\RENUP.exe" sau khi PyInstaller chay xong. Da khoi phuc version.txt ve v%CURRENT_VER%.
    del changelog.tmp 2>nul
    pause
    exit /b 1
)
echo.
echo [OK] Build --onedir thanh cong: %APP_STAGE%\RENUP.exe
echo.

REM Copy binary + presets can thiet vao thu muc stage - giong het buoc
REM tuong ung trong build.bat, chi khac dich la APP_STAGE\bin thay vi
REM RELEASE_DIR\bin.
xcopy /y /e /i "bin\codes" "%APP_STAGE%\bin\codes" >nul
copy /y "bin\ffmpeg.exe" "%APP_STAGE%\bin\ffmpeg.exe" >nul
copy /y "bin\ffprobe.exe" "%APP_STAGE%\bin\ffprobe.exe" >nul
copy /y "bin\yt-dlp.exe" "%APP_STAGE%\bin\yt-dlp.exe" >nul
REM Khoa Youtube Data API v3: dong goi san vao ban phat hanh de nguoi dung
REM cuoi khong phai nhap gi (ban dev nhap qua nut "Cai dat", ban phat hanh
REM khong co nut do). File nay NAM TRONG .gitignore - khoa khong bao gio vao
REM ma nguon hay lich su git, chi di tu dia sang goi cai.
REM Thieu khoa KHONG chan build: app van chay, chi la pha lay tieu de quay ve
REM dung yt-dlp va de dinh chan bot khi batch lon.
if exist "bin\yt_api_key.txt" (
    copy /y "bin\yt_api_key.txt" "%APP_STAGE%\bin\yt_api_key.txt" >nul
    echo [OK] Da dong goi khoa Youtube API vao ban phat hanh.
) else (
    echo [CANH BAO] Khong co bin\yt_api_key.txt - ban phat hanh se KHONG co khoa API.
    echo [CANH BAO] Pha lay tieu de se dung yt-dlp va co the bi chan bot khi batch lon.
    echo [CANH BAO] Muon co khoa: chay start.bat, bam nut "Cai dat", dan khoa roi Luu.
)

REM qjs.exe (QuickJS, ~2 MB): BAT BUOC cho chuc nang tai video Youtube ke tu
REM ADR-012. Youtube bat giai "n challenge" bang JavaScript o moi duong tai,
REM thieu file nay thi khong tai duoc video nao. Ten file phai dung 'qjs.exe'.
copy /y "bin\qjs.exe" "%APP_STAGE%\bin\qjs.exe" >nul
if not exist "%APP_STAGE%\bin\qjs.exe" (
    echo [LOI] Thieu bin\qjs.exe - chuc nang tai video Youtube se KHONG chay.
    echo [LOI] Tai tai: https://github.com/quickjs-ng/quickjs/releases/latest
    echo [LOI]   file qjs-windows-x86_64.exe, doi ten thanh qjs.exe, dat vao bin\
    pause
    exit /b 1
)

REM Don dep file/thu muc trung gian cua PyInstaller (khong dung xoa
REM APP_STAGE - do la ket qua build can giu lai cho buoc Inno Setup).
if exist "RENUP.spec" del "RENUP.spec"
if exist "%WORK_DIR%" rmdir /s /q "%WORK_DIR%"
if exist "__pycache__" rmdir /s /q "__pycache__"

REM ========================================
REM 5. Bien dich bo cai dat bang Inno Setup (ISCC.exe)
REM ========================================
for %%I in ("%~dp0..\RENUP_Setup") do set "SETUP_OUT_DIR=%%~fI"
if not exist "%SETUP_OUT_DIR%" mkdir "%SETUP_OUT_DIR%"

echo [BUILD] Dang bien dich bo cai dat bang Inno Setup...
echo [INFO] Thu muc xuat bo cai: %SETUP_OUT_DIR%
echo.
"%ISCC_PATH%" "installer\RENUP.iss" "/DAppStageDir=%APP_STAGE%" "/DSetupOutDir=%SETUP_OUT_DIR%"

if errorlevel 1 (
    echo %CURRENT_VER%> version.txt
    echo [LOI] Bien dich bo cai dat that bai ^(ISCC.exe bao loi^). Da khoi phuc version.txt ve v%CURRENT_VER% ^(version moi v%NEW_VER% CHUA duoc su dung^).
    del changelog.tmp 2>nul
    pause
    exit /b 1
)

set "SETUP_EXE=%SETUP_OUT_DIR%\RENUP_Setup_v%NEW_VER%.exe"
if not exist "%SETUP_EXE%" (
    echo %CURRENT_VER%> version.txt
    echo [LOI] Khong thay file cai dat sau khi ISCC.exe chay xong: "%SETUP_EXE%". Da khoi phuc version.txt ve v%CURRENT_VER%.
    del changelog.tmp 2>nul
    pause
    exit /b 1
)
echo.
echo [OK] Bien dich bo cai dat thanh cong: %SETUP_EXE%
echo.

REM ========================================
REM 6. Git commit and push (chi khi co git) - dung y het subroutine cua
REM build.bat, xem ghi chu chi tiet trong build.bat.
REM ========================================
if "%HAS_GIT%"=="1" (
    call :git_step
    if errorlevel 1 (
        echo [LOI] Buoc commit/push that bai - xem chi tiet loi git o tren. Dung script.
        echo [LOI] File cai dat da build xong tai "%SETUP_EXE%" nhung CHUA duoc dua len GitHub.
        pause
        exit /b 1
    )
) else (
    echo [BO QUA] Khong co git, bo qua buoc commit/push.
)
echo.

REM ========================================
REM 7. Tao GitHub Release (chi khi co gh) - dinh kem FILE CAI DAT
REM (RENUP_Setup_vX.Y.Z.exe), KHONG phai file RENUP.exe don le nhu
REM build.bat. Neu dung ca hai script (build.bat va build_installer.bat)
REM cho cung mot lan tang version, gh se bao loi "tag da ton tai" o lan
REM chay thu hai - day la hanh vi CO CHU DICH (tranh de mot tag GitHub
REM co 2 loai asset khac nhau ma khong ai de y): chi chon MOT trong hai
REM luong cho moi lan release.
REM ========================================
if "%HAS_GH%"=="1" (
    echo [RELEASE] Dang tao release v%NEW_VER%...

    echo ## RENUP v%NEW_VER% ^(Bo cai dat^)> release_notes.tmp
    echo.>> release_notes.tmp
    echo ### Thay doi>> release_notes.tmp
    type changelog.tmp >> release_notes.tmp

    gh release create "v%NEW_VER%" "%SETUP_EXE%" --title "RENUP v%NEW_VER%" --notes-file release_notes.tmp
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
    echo   File cai dat: %SETUP_EXE%
    echo   [BO QUA] Khong tao GitHub Release - thieu gh.
    echo ========================================
)

del changelog.tmp 2>nul

echo.
pause
exit /b 0

REM ========================================
REM Subroutine: git_commit_push (copy nguyen ban tu build.bat de hai
REM script khong lech logic commit/push theo thoi gian)
REM %1 = ten nhanh can push
REM Tra ve (qua "exit /b"): 0 = OK, 1 = that bai (commit that bai THAT
REM SU, hoac push that bai).
REM ========================================
REM ========================================
REM Subroutine: git_step
REM Do nhanh git hien tai, roi goi :git_commit_push.
REM
REM BAT BUOC nam trong subroutine, KHONG duoc nhet lai vao khoi
REM "if "%HAS_GIT%"=="1" ( ... )". Trong mot khoi ngoac, cmd thay gia tri
REM %CUR_BRANCH% ngay luc DOC ca khoi - tuc la TRUOC khi vong for kip
REM gan - nen no luon rong, va script luon ket luan sai la "khong xac
REM dinh duoc nhanh git" roi bo qua commit/push.
REM
REM Hau qua that: buoc commit/push chua tung chay lan nao, chi in mot
REM dong canh bao trong vo hai roi di tiep. Phat hien 2026-08-18, sau
REM khi hai release v1.1.24 va v1.1.25 len GitHub ma khong co commit
REM version.txt nao di kem (tag tren GitHub do gh tu tao nen tro vao
REM commit cu cua nhanh mac dinh, khong tro vao ma nguon that su da
REM dung nen bo cai).
REM
REM Trong subroutine, moi dong duoc doc va thuc thi lan luot nen
REM %CUR_BRANCH% co dung gia tri. Cach nay tranh phai bat
REM setlocal enabledelayedexpansion toan cuc - thu se nuot dau "!" don
REM le trong cac dong echo hien co.
REM ========================================
:git_step
set "CUR_BRANCH="
for /f "delims=" %%b in ('git rev-parse --abbrev-ref HEAD 2^>nul') do set "CUR_BRANCH=%%b"
if "%CUR_BRANCH%"=="" (
    echo [CANH BAO] Khong xac dinh duoc nhanh git hien tai ^(co the day khong phai git repo^). Bo qua buoc commit/push.
    exit /b 0
)
if /i "%CUR_BRANCH%"=="HEAD" (
    echo [CANH BAO] Dang o trang thai detached HEAD ^(khong nam tren nhanh nao^). Bo qua buoc commit/push de tranh day nham vao nhanh sai.
    exit /b 0
)
call :git_commit_push "%CUR_BRANCH%"
exit /b %ERRORLEVEL%

:git_commit_push
set "BRANCH_NAME=%~1"
echo [GIT] Commit v%NEW_VER% tren nhanh "%BRANCH_NAME%"...
git add -A
git diff --cached --quiet
if errorlevel 1 (
    git commit -m "release: v%NEW_VER% (installer)"
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
