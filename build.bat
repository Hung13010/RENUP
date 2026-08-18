@echo off
REM chcp 65001
echo ========================================
echo   RENUP - Build va Release
echo ========================================
echo.

REM ========================================
REM CANH BAO LUONG CU - doc truoc khi lam bat cu gi
REM ========================================
REM Su co that da xay ra (v1.1.23): ai do chay NHAM build.bat (luong cu,
REM dong goi MOT FILE DUY NHAT) thay vi build_installer.bat (luong chinh
REM thuc hien nay, dong goi kieu --onedir + bo cai dat Inno Setup). File
REM RENUP.exe mot-file 18.92MB bi day len GitHub Release. May nguoi dung
REM dang cai BAN CAI DAT (thu muc co file khoi chay nho + thu muc con
REM "_internal" chua thu vien) tu dong tai ban "cap nhat" nay ve va GHI DE
REM len file khoi chay cua ho. Ket qua: mot file kieu MOT-FILE nam canh
REM thu muc "_internal" kieu MOI - bo nap khong xac dinh duoc dang goi nao,
REM app CHET ngay khi mo voi loi:
REM   _PYI_APPLICATION_HOME_DIR environment variable is not defined!
REM Phai xoa 2 release khoi GitHub de khong ai khac dinh loi nay.
echo ========================================
echo   [CANH BAO] DAY LA LUONG CU (dong goi MOT FILE)
echo ========================================
echo   Luong PHAT HANH CHINH THUC bay gio la build_installer.bat
echo   (dong goi --onedir + bo cai dat Inno Setup).
echo.
echo   File build.bat nay CHI con dung de build thu MOT FILE .exe
echo   cuc bo (kiem tra nhanh, khong dai dien cho ban se phat hanh).
echo.
echo   Neu ban PHAT HANH nham file mot-file nay len GitHub, may nguoi
echo   dang dung ban CAI DAT se tu dong tai "cap nhat" ve, GHI DE len
echo   file khoi chay cua ho va lam HONG app cua ho ngay lap tuc
echo   (loi "_PYI_APPLICATION_HOME_DIR environment variable is not
echo   defined!"). Da xay ra that voi v1.1.23, phai xoa release de cuu.
echo.
echo   Neu ban dinh phat hanh phien ban moi, hay dung Ctrl+C ngay bay
echo   gio va chay build_installer.bat thay the.
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
REM
REM Luu y them: neu build thanh cong nhung nguoi dung KHONG xac nhan
REM phat hanh (xem buoc 5 ben duoi - buoc gate moi), version.txt CUNG
REM se duoc khoi phuc ve CURRENT_VER. Ly do: file build.bat nay bay gio
REM chi con dung de build thu cuc bo, khong con la luong phat hanh
REM chinh thuc; neu moi lan build thu deu "dot" mot so version that su
REM (du khong phat hanh gi), so version se nhay coc vo nghia trong
REM version.txt cua repo. Chi giu version MOI khi nguoi dung xac nhan
REM RO RANG muon phat hanh that.
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

REM ----------------------------------------------------------------------
REM Fix #024 - LOP PHONG VE 1: xoa RENUP.exe CU truoc khi goi PyInstaller.
REM Bug da xay ra that: neu PyInstaller hong SOM (vi du thieu module, loi
REM cu phap) va lan build TRUOC do da tung thanh cong, file RENUP.exe cu
REM van con nguyen trong RELEASE_DIR. Buoc kiem tra "if not exist" ben
REM duoi khi do van thay file "co ton tai" -> bao [OK] Build thanh cong
REM va script tiep tuc dem chinh file CU do di phat hanh len GitHub voi
REM version MOI. Nguoi dung tai ban cu ve, app khong chay duoc dung nhu
REM da xay ra. Xoa truoc dam bao "con file la con build that", khong con
REM sot lai tu lan chay khac.
REM ----------------------------------------------------------------------
if exist "%RELEASE_DIR%\RENUP.exe" del /f /q "%RELEASE_DIR%\RENUP.exe"

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

REM ----------------------------------------------------------------------
REM Fix #024 - LOP PHONG VE 2: kiem tra MA LOI cua chinh PyInstaller ngay
REM sau khi no chay ("if errorlevel 1" doc exit code THAT SU cua lenh vua
REM chay, danh gia tai thoi diem chay - khong phai %errorlevel% bi bay
REM delayed-expansion trong khoi ( ... )). Truoc day script CHI dua vao
REM "if not exist ...RENUP.exe" o duoi - mot minh no khong du (xem Lop
REM phong ve 1 o tren de biet vi sao "file ton tai" khong dong nghia voi
REM "build nay thanh cong"). Hai lop bo sung cho nhau:
REM   - Lop 1 (xoa truoc) dam bao khong con file CU de gay nham lan.
REM   - Lop 2 (ma loi) bat duoc truong hop PyInstaller that bai NGAY LAP
REM     TUC (kem theo "if not exist" o duoi bat them truong hop hiem:
REM     PyInstaller bao exit code 0 nhung vi ly do nao do van khong sinh
REM     ra file, vi du bi ngat giua chung ma khong tra ve loi).
REM ----------------------------------------------------------------------
if errorlevel 1 (
    echo %CURRENT_VER%> version.txt
    echo [LOI] PyInstaller bao loi ^(exit code != 0^). Da khoi phuc version.txt ve v%CURRENT_VER% ^(version moi v%NEW_VER% CHUA duoc su dung^).
    pause
    exit /b 1
)
if not exist "%RELEASE_DIR%\RENUP.exe" (
    echo %CURRENT_VER%> version.txt
    echo [LOI] Build that bai! Khong thay "%RELEASE_DIR%\RENUP.exe" sau khi PyInstaller chay xong. Da khoi phuc version.txt ve v%CURRENT_VER% ^(version moi v%NEW_VER% CHUA duoc su dung^).
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
REM 5. GATE PHAT HANH - bat buoc go dung chuoi xac nhan
REM Day la hang rao chinh de khong ai "lo tay" phat hanh nham luong
REM cu nay len GitHub nua (xem canh bao dau file). Buoc commit/push
REM (6) va tao GitHub Release (7) ben duoi CHI chay khi nguoi dung go
REM DUNG chuoi xac nhan - khong phai bam phim bat ky (Enter khong tinh
REM la dong y, phai bang chu).
REM
REM Xu ly stdin can (giong het co che da dung o vong lap changelog o
REM buoc 3): dung gia tri mot "khong the go duoc" lam mac dinh cho bien
REM truoc khi doc. Neu "set /p" khong doc duoc gi (stdin da can, vi du
REM script duoc goi tu mot tien trinh khong co input), bien van giu
REM nguyen gia tri mac dinh do -> code hieu la CHUA XAC NHAN va TU DONG
REM HUY buoc phat hanh, KHONG treo may, KHONG coi nhu dong y.
REM ========================================
echo ========================================
echo   BUOC PHAT HANH ^(GitHub^)
echo ========================================
echo   Build .exe MOT-FILE da xong tai:
echo     %RELEASE_DIR%\RENUP.exe
echo.
echo   Buoc tiep theo se COMMIT + PUSH len git va TAO GITHUB RELEASE
echo   voi dung file mot-file nay. Nhu canh bao o dau script: neu ai do
echo   dang dung ban CAI DAT, ho se bi ghi de file khoi chay va app cua
echo   ho se HONG.
echo.
echo   Chi tiep tuc neu ban CHAC CHAN muon phat hanh bang luong cu nay
echo   (thuong thi KHONG - hay dung build_installer.bat thay the).
echo.
echo   De XAC NHAN phat hanh, go dung chuoi sau roi Enter:
echo       XAC NHAN PHAT HANH
echo   Go bat ky thu gi khac, de trong, hoac Enter khi khong co input,
echo   se HUY buoc phat hanh ^(build .exe cuc bo van giu nguyen^).
echo.
set "RELEASE_CONFIRM=__EOF_SENTINEL__"
set /p "RELEASE_CONFIRM=  > "
if "%RELEASE_CONFIRM%"=="XAC NHAN PHAT HANH" (
    set "DO_RELEASE=1"
) else (
    set "DO_RELEASE=0"
)

if "%DO_RELEASE%"=="0" (
    echo.
    if "%RELEASE_CONFIRM%"=="__EOF_SENTINEL__" (
        echo [CANH BAO] Stdin ket thuc som ^(khong doc duoc xac nhan^). Coi nhu HUY phat hanh.
    ) else (
        echo [INFO] Khong khop chuoi xac nhan. Huy buoc phat hanh.
    )
    echo [INFO] Khoi phuc version.txt ve v%CURRENT_VER% ^(version moi v%NEW_VER% CHUA duoc su dung, khong "dot" so vi build nay khong phat hanh^).
    echo %CURRENT_VER%> version.txt
    del changelog.tmp 2>nul
    echo.
    echo ========================================
    echo   [OK] BUILD THU CUC BO XONG - KHONG PHAT HANH
    echo   File: %RELEASE_DIR%\RENUP.exe
    echo   ^(File nay van dung "v%NEW_VER%" ben trong vi da build roi -
    echo    chi version.txt trong source la duoc khoi phuc^)
    echo   Muon phat hanh that? Dung build_installer.bat.
    echo ========================================
    echo.
    pause
    exit /b 0
)
echo [OK] Da xac nhan phat hanh. Tiep tuc...
echo.

REM ========================================
REM 6. Git commit and push (chi khi co git)
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
    call :git_step
    if errorlevel 1 (
        echo [LOI] Buoc commit/push that bai - xem chi tiet loi git o tren. Dung script.
        echo [LOI] File RENUP.exe da build xong tai "%RELEASE_DIR%" nhung CHUA duoc dua len GitHub.
        pause
        exit /b 1
    )
) else (
    echo [BO QUA] Khong co git, bo qua buoc commit/push.
)
echo.

REM ========================================
REM 7. Tao GitHub Release (chi khi co gh)
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
REM Day chinh la bay ma ghi chu o muc 6 noi toi, nhung lan truoc moi
REM chi don PHAN commit/push vao subroutine, con phan DO NHANH thi van
REM ket lai trong khoi ngoac - nen bay van con nguyen. Hau qua: buoc
REM commit/push chua tung chay lan nao, chi in mot dong canh bao trong
REM vo hai roi di tiep. Phat hien 2026-08-18, sau khi hai release
REM v1.1.24 va v1.1.25 len GitHub ma khong co commit version.txt di kem.
REM
REM Trong subroutine, moi dong duoc doc va thuc thi lan luot nen
REM %CUR_BRANCH% co dung gia tri. Cach nay tranh phai bat
REM setlocal enabledelayedexpansion toan cuc - thu se nuot dau "!" don
REM le trong cac dong echo hien co (vd "Build that bai!").
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
