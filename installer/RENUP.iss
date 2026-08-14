; ============================================================================
;  RENUP.iss - Inno Setup script cho bo cai dat RENUP (dong goi --onedir)
;
;  KHONG bien dich file nay truc tiep trong da so truong hop. Dung
;  "build_installer.bat" o thu muc goc RENUP_build\ - no se:
;    1) Build RENUP bang PyInstaller --onedir vao dist_installer\RENUP\
;    2) Copy bin\ffmpeg.exe, bin\ffprobe.exe, bin\yt-dlp.exe, bin\codes\
;       vao dist_installer\RENUP\bin\ (giong build.bat)
;    3) Goi ISCC.exe kem "/DAppStageDir=..." va "/DSetupOutDir=..." tro
;       dung thu muc vua build o buoc 1-2 va thu muc xuat file cai dat
;
;  Neu ban thuc su can bien dich thu cong (vi du trong Inno Setup IDE) thi
;  phai tu chay PyInstaller --onedir truoc; gia tri mac dinh AppStageDir/
;  SetupOutDir ben duoi se tu tro ve dist_installer\RENUP canh script nay.
;
;  ==========================================================================
;  CANH BAO QUAN TRONG VE AppId (doc truoc khi dung tay vao file nay):
;
;  GUID trong MyAppId la DINH DANH DUY NHAT cua RENUP doi voi Windows
;  (Add/Remove Programs, Start Menu, registry key cua uninstaller). GUID
;  nay LA CO DINH VA TUYET DOI KHONG DUOC DOI sau khi da phat hanh ban cai
;  dat dau tien cho nguoi dung that. Neu doi:
;    - Windows se coi ban cai moi la MOT UNG DUNG KHAC HOAN TOAN, khong
;      nhan ra va khong ghi de duoc ban da cai truoc do.
;    - Nguoi dung se co HAI thu muc RENUP song song tren may (ban cu +
;      ban moi), moi ban co uninstaller rieng - rat de gay nham lan va
;      rac du lieu (Noi.txt, claim_state.json rieng cho tung ban).
;    - Uninstaller cua ban cu se "mo coi", khong con lien ket voi ban moi.
;  ==========================================================================
; ============================================================================

#define MyAppName "RENUP"
#define MyAppPublisher "RENUP"
#define MyAppExeName "RENUP.exe"
#define MyAppURL "https://github.com/Hung13010/RENUP"

; GUID CO DINH - XEM CANH BAO O TREN DAU FILE. TUYET DOI KHONG TU SINH GUID
; MOI HAY THAY DOI GIA TRI NAY.
#define MyAppId "{A7E4F8B2-6C3D-4E1A-9F7B-2D8C5A1E3F09}"

; ---- Doc phien ban tu version.txt (nguon su that duy nhat, dung chung voi
;      build.bat va build_installer.bat) de khong phai sua tay hai noi moi
;      lan release. version.txt nam canh thu muc installer\ nay (mot cap
;      thu muc len). ----
#define MyAppVersion Trim(FileRead(FileOpen(SourcePath + "..\version.txt")))

; ---- Thu muc nguon (--onedir stage) + thu muc xuat file cai dat. Binh
;      thuong duoc build_installer.bat truyen vao qua tham so dong lenh
;      "/DAppStageDir=..." va "/DSetupOutDir=...". Gia tri mac dinh o day
;      chi ap dung khi bien dich thu cong (khong qua build_installer.bat). ----
#ifndef AppStageDir
  #define AppStageDir SourcePath + "..\dist_installer\RENUP"
#endif
#ifndef SetupOutDir
  #define SetupOutDir SourcePath + "..\..\RENUP_Setup"
#endif

[Setup]
AppId={{#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}

; Cai vao thu muc rieng cua user (LocalAppData), KHONG phai Program Files.
; Day la RANG BUOC KY THUAT BAT BUOC, khong phai so thich: RENUP ghi du
; lieu vao chinh thu muc cai dat cua no luc chay - bin\Noi.txt,
; bin\claim_state.json, bin\_yt_thumb_cache\, va thu muc output mac dinh.
; Neu cai vao Program Files, Windows chan ghi khi chay khong co quyen
; admin va cac chuc nang do se hong ngay.
DefaultDirName={localappdata}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest

; Neu RENUP dang chay luc cai/cai lai, tu dong phat hien (qua Windows
; Restart Manager) file dang bi khoa boi RENUP.exe roi de nghi dong app
; truoc khi ghi de.
;   - Cai thu cong (khong co /FORCECLOSEAPPLICATIONS tren dong lenh):
;     CloseApplications=yes khong tu dong dong gi ca - Setup hien trang
;     "Dang su dung boi ung dung khac" cho nguoi dung tu quyet dinh dong
;     hay huy cai dat. Giu dung trai nghiem cai thu cong binh thuong.
;   - Cai ngam (co dong lenh /FORCECLOSEAPPLICATIONS - dung boi nut cap
;     nhat trong app): co dong lenh nay se GHI DE hanh vi tren, buoc dong
;     app ngay khong hoi gi (khong co trang wizard nao de hoi trong che do
;     /VERYSILENT). /FORCECLOSEAPPLICATIONS CHI co tac dung khi
;     CloseApplications khac "no" - vi vay bat buoc phai giu "yes" (khong
;     duoc doi thanh "no") de co che ngam nay hoat dong.
CloseApplications=yes

; RestartApplications=no LA CHU DICH, khong phai thieu sot - xem giai
; thich chi tiet + ly do khong dung /RESTARTAPPLICATIONS cua Windows
; Restart Manager tai muc [Run] ben duoi (tim "KHOI CHAY LAI APP SAU KHI
; CAI NGAM").
RestartApplications=no

ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

OutputDir={#SetupOutDir}
OutputBaseFilename=RENUP_Setup_v{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile={#SourcePath}..\icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
; Khong khai bao flag "unchecked" -> mac dinh CO tick san (yeu cau: "co
; tuy chon, mac dinh bat").
Name: "desktopicon"; Description: "Tao shortcut ngoai Desktop"; GroupDescription: "Icon bo sung:"

[Files]
; Toan bo thu muc --onedir: RENUP.exe + thu muc phu thuoc PyInstaller sinh
; ra (_internal\... hoac tuong duong tuy phien ban PyInstaller) + bin\...
; (ffmpeg.exe, ffprobe.exe, yt-dlp.exe, bin\codes\...) da duoc
; build_installer.bat copy san vao AppStageDir TRUOC khi ISCC.exe chay.
; "ignoreversion" vi day la app noi bo dung rieng, khong can Windows so
; sanh version tung file DLL mot voi ban da cai.
Source: "{#AppStageDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{userdesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; ============================================================================
; KHOI CHAY LAI APP SAU KHI CAI NGAM (phuc vu nut "Cap nhat" trong app - app
; tu tai bo cai roi chay lai voi /VERYSILENT /SUPPRESSMSGBOXES /NORESTART
; /FORCECLOSEAPPLICATIONS /RESTARTAPPLICATIONS).
;
; VAN DE: flag "postinstall" (dong ben duoi) gan viec khoi chay app vao
; checkbox tren trang Finished cua wizard. Trang Finished KHONG TON TAI
; trong che do /SILENT hoac /VERYSILENT - vi vay Inno Setup luon dinh kem
; flag "skipifsilent" cho loai dong nay, va dong co "skipifsilent" se BI
; BO QUA HOAN TOAN khi cai ngam. Ket qua: neu chi co mot dong [Run] duy
; nhat (nhu ban goc), nguoi dung bam "Cap nhat" trong app se thay app
; DONG lai (do bi dong de ghi de file) va KHONG BAO GIO tu mo lai - giong
; nhu app bi hong.
;
; GIAI PHAP: hai dong [Run] tach biet, loai tru lan nhau qua Check:
;   1) Dong "postinstall skipifsilent" ben duoi - CHI chay khi cai
;      TUONG TAC (thu cong): hien checkbox tren trang Finished, mac dinh
;      da tick san (khong co flag "unchecked"), nguoi dung van co the bo
;      tick neu khong muon mo app ngay. Dung y nhu ban goc.
;   2) Dong thu hai (khong co "postinstall", khong co "skipifsilent") -
;      CHI chay khi cai NGAM, nho dieu kien "Check: WizardSilent".
;      WizardSilent la ham co san cua Inno Setup (khong can khai bao
;      [Code]), tra ve True khi Setup dang chay voi /SILENT hoac
;      /VERYSILENT. Dong nay khong gan "postinstall" nen no chay ngay sau
;      khi cai xong ma KHONG can trang wizard nao ca - dung nhu yeu cau
;      "cai ngam la xong xuoi tu dong, app tu mo lai".
;
; Ca hai dong deu co "nowait" - Setup khong doi RENUP.exe dong lai moi
; thoat, tranh treo tien trinh cap nhat phia app dang goi Setup.
;
; VE /RESTARTAPPLICATIONS (Windows Restart Manager): script nay CO CHU
; DICH dat RestartApplications=no o [Setup] ben tren, khien co chuoi
; /RESTARTAPPLICATIONS ma app truyen vao TRO THANH VO HIEU (Inno Setup
; noi ro: co "/RESTARTAPPLICATIONS" khong co tac dung gi neu
; RestartApplications=no trong script). Day la lua chon co chu dich, KHONG
; phai loi:
;   - Restart Manager chi tu dong mo lai app NEU no da tu dong DONG app
;     do luc cai (tuc la chi hoat dong khi RENUP dang chay san). Neu
;     nguoi dung bam "Cap nhat" luc RENUP KHONG chay (vi du: mo bo cai
;     tu ngoai), Restart Manager se khong co gi de "mo lai" ca - khong
;     dam bao duoc yeu cau "app luon tu mo lai sau khi cap nhat".
;   - Dong [Run] thu hai o duoi (Check: WizardSilent) LUON chay sau MOI
;     lan cai ngam, bat ke RENUP co dang chay truoc do hay khong -> dam
;     bao chac chan hon co che Restart Manager.
;   - Neu bat ca RestartApplications=yes LAN dong [Run] thu hai cung luc,
;     app co the bi mo HAI LAN (mot boi Restart Manager, mot boi [Run]) -
;     day la ly do KHONG duoc bat RestartApplications=yes trong khi van
;     giu dong [Run] thu hai.
; Ket luan: khong can sua gi ben app (/RESTARTAPPLICATIONS trong chuoi
; lenh cu cua app la vo hai du khong con tac dung), va KHONG can doi
; RestartApplications trong script nay.
; ============================================================================
Filename: "{app}\{#MyAppExeName}"; Description: "Khoi chay {#MyAppName} ngay"; Flags: nowait postinstall skipifsilent
Filename: "{app}\{#MyAppExeName}"; Flags: nowait; Check: WizardSilent
