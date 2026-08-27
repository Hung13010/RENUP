import sys
import os
import re
import time
import collections
import subprocess
import threading
import json
import hashlib
import random
import urllib.request
import urllib.error
import urllib.parse
import webbrowser
import uuid
import io
import base64
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from PIL import Image

import webview


def get_app_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def get_bundle_dir():
    return getattr(sys, '_MEIPASS', get_app_dir())


def format_size(size_bytes):
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


GITHUB_REPO = "Hung13010/RENUP"
GITHUB_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"

# Chi nhan asset dung dinh dang bo cai dat Inno Setup (installer\RENUP.iss,
# OutputBaseFilename=RENUP_Setup_v<version>.exe). File .exe dau tien gap
# duoc trong release gio la BO CAI DAT, khong con la ban than app nua - lay
# nham se pha huong tu cap nhat (shortcut cua nguoi dung mo ra cua so cai
# dat thay vi ung dung, hoac te hon: file chay goc bi xoa mat).
UPDATE_ASSET_RE = re.compile(r'^RENUP_Setup_v\d+(?:\.\d+)*\.exe$', re.IGNORECASE)


# ── Youtube Download: regex constants (module-level so they compile once) ──
YT_ID_PATTERNS = [
    re.compile(r'youtu\.be/([A-Za-z0-9_-]{11})'),
    re.compile(r'youtube\.com/watch\?(?:.*&)?v=([A-Za-z0-9_-]{11})'),
    re.compile(r'youtube\.com/shorts/([A-Za-z0-9_-]{11})'),
    re.compile(r'youtube\.com/live/([A-Za-z0-9_-]{11})'),
    re.compile(r'youtube\.com/embed/([A-Za-z0-9_-]{11})'),
]
YT_ID_BARE = re.compile(r'^[A-Za-z0-9_-]{11}$')
YT_QUALITIES = ('Best', '2160p', '1440p', '1080p', '720p', '480p', '360p')
RE_YT_DL = re.compile(r'^\[download\]\s+(\d{1,3}(?:\.\d+)?)%')
# Moi luot tai mot luong (video, roi audio) mo dau bang mot dong Destination.
# Dung no de dem "pha" thay vi doan qua viec phan tram tut xuong - phep doan do
# sai khi yt-dlp tiep tuc mot file da tai do dang (bat dau tu 40% chang han).
RE_YT_DEST = re.compile(r'^\[download\]\s+Destination:')
# "at 10.83MiB/s" / "at 999.04KiB/s". CO Y KHONG khop "at Unknown B/s" (yt-dlp in
# the o vai dong dau khi chua do duoc toc do) - khong hien gi con hon hien "Unknown".
RE_YT_SPEED = re.compile(r'\sat\s+([\d.]+\s*[KMGT]?i?B/s)')
# ThumbnailsConvertor CO Y bi loai khoi danh sach nay. Do thuc te (2026-08-22):
# anh bia duoc tai va chuyen doi TRUOC khi video bat dau tai, nen coi no la dau
# hieu "sap xong" se day thanh tien trinh len 92% ngay tu dau, va vi code chi
# cap nhat khi phan tram TANG nen moi dong [download] xx% sau do (toi da 70) deu
# bi bo qua - thanh do nam im o 92% suot ca lan tai. Xem TODO #045.
RE_YT_POST = re.compile(r'^\[(Merger|ExtractAudio|VideoRemuxer|FixupM3u8|Fixup\w*)\]')
# Che do "chi tai mot doan" (--download-sections) giao viec tai cho ffmpeg, nen
# KHONG con dong "[download] xx%" nao trong suot qua trinh. Tien do that nam o
# stderr cua ffmpeg: "frame=150 ... time=00:00:09.98 bitrate=..." - time= la moc
# da xu ly xong, so voi do dai doan da yeu cau se ra phan tram that.
RE_FF_TIME = re.compile(r'\btime=\s*(\d+):(\d\d):(\d\d(?:\.\d+)?)')
RE_FF_SIZE = re.compile(r'\b[A-Za-z]*size=\s*(\d+)kB')
# Dong tien do cua ffmpeg lap lai moi giay; giu chung trong bo dem loi se day
# nguyen nhan that ra khoi 5 dong cuoi ma _yt_download_one dung de bao loi.
RE_FF_PROGRESS_LINE = re.compile(r'^(frame|size)=')
# Claim Tiktok - cac moc that trong dau ra cua yt-dlp khi tai voice. Dung de neo
# thanh tien trinh; xem ghi chu trong _download_voice_tiktok ve ly do khong bam
# thang vao "[download] xx%" (khau tai chi chiem 0.6% thoi gian cua buoc nay).
RE_TT_EXTRACT = re.compile(r'^\[tiktok:\w+\]\s+Extracting URL')
RE_TT_LIST = re.compile(
    r'^\[(?:tiktok:\w+|download)\]\s+.*(?:Downloading video list|Downloading \d+ items'
    r'|Downloading item |Downloading playlist)')
RE_TT_AUDIO = re.compile(r'^\[ExtractAudio\]')

# ── Youtube Thumbnail (ADR-008): module-level constants ──
YT_THUMB_LADDER = ['maxresdefault', 'sddefault', 'hqdefault', 'mqdefault', 'default']
YT_THUMB_NOMINAL = {
    'maxresdefault': (1280, 720), 'sddefault': (640, 480),
    'hqdefault': (480, 360), 'mqdefault': (320, 180), 'default': (120, 90),
}
YT_THUMB_URL = "https://i.ytimg.com/vi/{vid}/{rung}.jpg"
YT_THUMB_FORMATS = {'JPG': '.jpg', 'PNG': '.png', 'WEBP': '.webp'}
YT_THUMB_PUSH_CHUNK = 20
YT_CHANNEL_TABS = ('videos', 'shorts', 'streams', 'playlists', 'featured', 'community', 'about')
RE_YT_CHANNEL = re.compile(
    r'youtube\.com/(?:@[^/?#\s]+|channel/[^/?#\s]+|c/[^/?#\s]+|user/[^/?#\s]+)', re.I)
RE_YT_PLAYLIST = re.compile(r'youtube\.com/playlist\?', re.I)


def get_version():
    for d in [get_bundle_dir(), get_app_dir()]:
        vf = os.path.join(d, 'version.txt')
        if os.path.exists(vf):
            with open(vf, 'r') as f:
                return f.read().strip()
    return "1.1.0"


# ══════════════════════════════════════════════════════════════
# API class — exposed to JavaScript via pywebview
# ══════════════════════════════════════════════════════════════

class Api:
    RETRY_SIG_IGNORE = {'workers'}   # ADR-007: cac khoa loai khoi chu ky dau vao

    def __init__(self):
        app_dir = get_app_dir()
        self.bin_dir = os.path.join(app_dir, 'bin')
        self.codes_dir = os.path.join(self.bin_dir, 'codes')
        self.ffmpeg_path = os.path.join(self.bin_dir, 'ffmpeg.exe')
        self.ffprobe_path = os.path.join(self.bin_dir, 'ffprobe.exe')
        self.noi_txt_path = os.path.join(self.bin_dir, 'Noi.txt')
        self.claim_state_path = os.path.join(self.bin_dir, 'claim_state.json')
        # ADR-015: khoa Youtube Data API v3 do NGUOI DUNG tu tao. La thong tin
        # nhay cam nen KHONG duoc nam trong ma nguon hay trong preset JSON di
        # kem bo cai - luu rieng ra file nay, va file nay nam trong .gitignore.
        # Dat trong bin/ giong claim_state.json: bo cai khong mang file nay nen
        # cai de len khong xoa mat khoa da luu.
        self.yt_api_key_path = os.path.join(self.bin_dir, 'yt_api_key.txt')
        self.ytdlp_path = os.path.join(self.bin_dir, 'yt-dlp.exe')
        # ADR-012: QuickJS di kem app. Youtube tu ~2026-08 bat giai "n challenge"
        # bang JavaScript nen MOI duong tai deu can mot JS runtime; qjs.exe chi
        # 2 MB (Node 88 MB, Deno con lon hon) nen dong goi duoc ma khong lam
        # phinh dang ke. Ten file BUOC phai la 'qjs.exe' - yt-dlp tim dung ten do.
        self.qjs_path = os.path.join(self.bin_dir, 'qjs.exe')
        self.is_running = False
        self._paused = False
        self._stopped = False
        self._current_procs = []
        self._lock = threading.Lock()
        self._window = None
        self._code_map = {}
        self._pending_tasks = []    # [(idx, task_fn), ...]
        self._task_results = {}     # {idx: True/False}
        self._current_executor = None
        self._run_params = None

        # ── ADR-008: Youtube thumbnail ──
        self._yt_thumb_items = []      # list[dict] - catalogue sau lan Load gan nhat, giu thu tu
        self._yt_thumb_index = {}      # dict[str, dict] - video_id -> item (tra cuu O(1))
        self._yt_thumb_dir = os.path.join(self.bin_dir, '_yt_thumb_cache')

        # ── ADR-007: retry failed rows ──
        self._retry_sig = None          # str|None - chu ky dau vao cua lan chay truoc
        self._retry_failed = []         # list[str] - nhan cac dong loi lan truoc, giu thu tu
        self._retry_total = 0           # int - tong so dong lan truoc (chi de hien thi trong popup)
        self._retry_mode = 'all'        # 'all' | 'failed' - che do cua lan chay HIEN TAI
        self._retry_targets = None      # set[str]|None - None = chay tat ca
        self._batch_outcomes = {}       # dict[str,bool] - nhan -> thanh cong, cua lan chay hien tai
        self._batch_labels = None       # list[str]|None - toan bo nhan cua lan chay hien tai
        self._retry_choice = ''         # '' | 'failed' | 'all' | 'cancel'
        self._retry_event = threading.Event()

        # ── Auto-update (Inno Setup installer) ──
        self._update_in_progress = False

    def set_window(self, window):
        self._window = window

    def _js(self, code):
        if self._window:
            self._window.evaluate_js(code)

    def pause(self):
        if not self.is_running:
            return
        self._paused = not self._paused
        self._js(f"uiApi.setPaused({str(self._paused).lower()})")
        if self._paused:
            # Suspend running ffmpeg processes
            for proc in self._current_procs:
                self._suspend_process(proc.pid)
            self._log("=== TAM DUNG (co the thay doi so luong) ===", 'info')
            self._js("uiApi.setStatus('Tam dung...')")
        else:
            # Resume running ffmpeg processes
            for proc in self._current_procs:
                self._resume_process(proc.pid)
            # Read new worker count from UI and restart pending tasks
            new_workers = self._window.evaluate_js("parseInt(document.getElementById('workers').value) || 1")
            self._log(f"=== TIEP TUC voi {new_workers} luong ===", 'info')
            self._js("uiApi.setStatus('Dang xu ly...')")
            # If there are pending tasks, start a new batch with new worker count
            if self._pending_tasks:
                threading.Thread(target=self._run_pending_tasks, args=(int(new_workers),), daemon=True).start()

    def _suspend_process(self, pid):
        """Suspend a process by suspending all its threads."""
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            ntdll = ctypes.windll.ntdll
            handle = kernel32.OpenProcess(0x1F0FFF, False, pid)  # PROCESS_ALL_ACCESS
            if handle:
                ntdll.NtSuspendProcess(handle)
                kernel32.CloseHandle(handle)
        except Exception:
            pass

    def _resume_process(self, pid):
        """Resume a suspended process."""
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            ntdll = ctypes.windll.ntdll
            handle = kernel32.OpenProcess(0x1F0FFF, False, pid)  # PROCESS_ALL_ACCESS
            if handle:
                ntdll.NtResumeProcess(handle)
                kernel32.CloseHandle(handle)
        except Exception:
            pass

    def stop(self):
        if not self.is_running:
            return
        self._stopped = True
        self._paused = False
        self._kill_all_ffmpeg()
        self._log("=== DA DUNG ===", 'err')
        self._js("uiApi.setStatus('Da dung.')")

    def _kill_all_ffmpeg(self):
        """Kill all tracked processes AND their descendants.

        yt-dlp spawns its own ffmpeg child for remuxing (--ffmpeg-location); on
        Windows, proc.kill() only terminates the tracked PID itself, not that
        child, leaving ffmpeg orphaned and still downloading/writing to disk
        after Stop/close. 'taskkill /T' kills the whole process tree instead.
        """
        for proc in self._current_procs:
            try:
                self._resume_process(proc.pid)
            except Exception:
                pass
            try:
                subprocess.run(
                    ['taskkill', '/F', '/T', '/PID', str(proc.pid)],
                    capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW, timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
            try:
                proc.wait(timeout=5)
            except Exception:
                pass
        self._current_procs.clear()

    def cleanup(self):
        """Called when app is closing. Kill all FFmpeg processes."""
        self._stopped = True
        self._kill_all_ffmpeg()

    def _run_pending_tasks(self, workers):
        """Run remaining pending tasks with given worker count."""
        tasks = list(self._pending_tasks)
        self._pending_tasks.clear()
        if not tasks:
            return

        self._log(f"Chay {len(tasks)} task con lai voi {workers} luong.", 'info')

        with ThreadPoolExecutor(max_workers=workers) as ex:
            self._current_executor = ex
            submitted = {}
            not_submitted = list(tasks)

            # Submit initial batch
            for idx, task_fn in tasks:
                if self._stopped or self._paused:
                    break
                submitted[ex.submit(task_fn)] = (idx, task_fn)
                not_submitted.remove((idx, task_fn))

            # Process completed futures
            for f in as_completed(submitted):
                idx, task_fn = submitted[f]
                try:
                    success, _ = f.result()
                except:
                    success = False
                self._task_results[idx] = success
                self._update_task_progress(idx, success)

                # If paused during execution, put remaining not-submitted back
                if self._paused or self._stopped:
                    break

            # Put unsubmitted tasks back to pending
            if not_submitted:
                self._pending_tasks.extend(not_submitted)

            self._current_executor = None

    def _update_task_progress(self, idx, success):
        """Update process table and progress bar for completed task."""
        self._mark_row(idx, success)
        with self._lock:
            done = sum(1 for v in self._task_results.values())
            ok = sum(1 for v in self._task_results.values() if v)
            total = self._total_tasks
        if total > 0:
            self._js(f"uiApi.setProgress({int(done/total*100)}, '{done}/{total}')")
            self._js(f"uiApi.setStatus('Dang xu ly... {done}/{total}')")

    def _log(self, msg, tag=''):
        safe = msg.replace('\\', '\\\\').replace("'", "\\'").replace('\n', '\\n')
        self._js(f"uiApi.log('{safe}', '{tag}')")

    # ── Init ──

    def init(self):
        self._load_codes()
        self._js(f"uiApi.setVersion('{get_version()}')")
        self._js(f"uiApi.setCodes({json.dumps(self._code_groups)})")
        # Dev mode = running from source, not from PyInstaller bundle
        is_dev = not getattr(sys, 'frozen', False)
        self._js(f"uiApi.setDevMode({str(is_dev).lower()})")
        # Trigger UI update for the initially selected function
        self._js("onFuncChanged()")
        self._load_noi_txt()
        self._load_claim_state()
        # Khoi phuc khoa API da luu vao o nhap (o dang password nen khong lo ra
        # man hinh). Khong log gi o day - im lang la dung, va tuyet doi khong
        # duoc ghi khoa ra log.
        _k = self._load_yt_api_key()
        if _k:
            self._js("var e=document.getElementById('ytApiKey');"
                     f"if(e) e.value = {json.dumps(_k)};")
        threading.Thread(target=self._check_update, daemon=True).start()

    def restart(self):
        """Dev-mode reload: spawn a fresh Python process and exit current one."""
        if getattr(sys, 'frozen', False):
            cmd = [sys.executable]
        else:
            cmd = [sys.executable, os.path.abspath(sys.argv[0])]
        try:
            subprocess.Popen(cmd, creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP, close_fds=True)
        except Exception as e:
            self._log(f"Loi spawn process moi: {e}", 'err')
            return
        # Give the new process a moment to start, then teardown current one
        threading.Timer(0.4, self._teardown_for_restart).start()

    def _teardown_for_restart(self):
        try:
            self._kill_all_ffmpeg()
        except Exception:
            pass
        try:
            self._window.destroy()
        except Exception:
            os._exit(0)

    def _load_codes(self):
        self._code_map = {}
        self._code_groups = []
        os.makedirs(self.codes_dir, exist_ok=True)

        for entry in sorted(os.listdir(self.codes_dir)):
            entry_path = os.path.join(self.codes_dir, entry)

            if os.path.isdir(entry_path):
                # Subfolder = category
                category = entry
                items = []
                for f in sorted(os.listdir(entry_path)):
                    if f.lower().endswith('.json'):
                        try:
                            with open(os.path.join(entry_path, f), 'r', encoding='utf-8') as fh:
                                data = json.load(fh)
                            name = data.get('name', os.path.splitext(f)[0])
                            self._code_map[name] = data
                            items.append(name)
                        except Exception:
                            pass
                if items:
                    self._code_groups.append({"category": category, "items": items})

            elif entry.lower().endswith('.json'):
                # Root-level json (no category)
                try:
                    with open(entry_path, 'r', encoding='utf-8') as fh:
                        data = json.load(fh)
                    name = data.get('name', os.path.splitext(entry)[0])
                    self._code_map[name] = data
                    self._code_groups.append({"category": None, "items": [name]})
                except Exception:
                    pass

    def _load_noi_txt(self):
        if os.path.exists(self.noi_txt_path):
            with open(self.noi_txt_path, 'r', encoding='utf-8') as f:
                content = f.read()
            safe = content.replace('\\', '\\\\').replace("'", "\\'").replace('\n', '\\n')
            self._js(f"uiApi.setEditor('{safe}')")
            self._log("Da tai Noi.txt.", 'info')

    def saveClaimState(self, state):
        """Save Claim Tiktok UI state (voiceDir, musicDir, outputDir, workers) to claim_state.json.

        Called by the frontend whenever any of the fields changes.
        Does not log on success to avoid noise (frontend calls this frequently).
        """
        try:
            data = {
                'voiceDir': state.get('voiceDir', '') if isinstance(state, dict) else '',
                'musicDir': state.get('musicDir', '') if isinstance(state, dict) else '',
                'outputDir': state.get('outputDir', '') if isinstance(state, dict) else '',
                'workers': state.get('workers', '') if isinstance(state, dict) else '',
            }
            with open(self.claim_state_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False)
        except Exception as e:
            self._log(f"Loi luu claim state: {e}", 'err')

    def saveYtApiKey(self, key):
        """Luu khoa Youtube Data API v3 vao bin/yt_api_key.txt.

        KHONG BAO GIO ghi khoa ra log - chi bao da luu hay da xoa. Log cua app
        hien tren man hinh va nguoi dung hay chup man hinh gui di.
        """
        try:
            key = str(key or '').strip()
            if not key:
                if os.path.exists(self.yt_api_key_path):
                    os.remove(self.yt_api_key_path)
                self._log("Da xoa khoa API Youtube.", 'info')
                return
            with open(self.yt_api_key_path, 'w', encoding='utf-8') as f:
                f.write(key)
            self._log(f"Da luu khoa API Youtube ({len(key)} ky tu).", 'ok')
        except Exception as e:
            self._log(f"Loi luu khoa API: {e}", 'err')

    def checkYtApiKey(self, key):
        """Kiem tra khoa API con song khong, bang MOT loi goi that.

        Chay o thread nen vi co I/O mang. Ket qua tra ve UI qua
        setYtApiKeyResult(). Neu o nhap de trong thi dung khoa da luu tren dia
        - de kiem duoc chinh cai khoa se duoc dong goi luc build.

        KHONG BAO GIO ghi khoa ra log hay ra man hinh ket qua.
        """
        def work():
            k = str(key or '').strip() or self._load_yt_api_key()
            if not k:
                self._js("setYtApiKeyResult('Chua co khoa. Dan khoa vao o tren roi bam Kiem tra.', 'err')")
                return
            # Dung mot video cong khai lau doi lam phep thu. Lay duoc tieu de
            # nghia la ca chuoi deu thong: khoa hop le, API da bat, con han muc.
            probe_id = 'jNQXAC9IVRw'
            titles = self._yt_fetch_titles_api([probe_id], k, 20)
            if titles.get(probe_id):
                msg = (f"Khoa hoat dong tot.\\nTra ve: {titles[probe_id]}\\n"
                       f"Da luu {len(k)} ky tu - se duoc dong goi khi build.")
                self._js(f"setYtApiKeyResult({json.dumps(msg)}, 'ok')")
            else:
                msg = ("Khoa KHONG dung duoc. Xem dong loi o Log ben duoi de biet"
                       " ly do (khoa sai, chua bat YouTube Data API v3, hoac het han muc).")
                self._js(f"setYtApiKeyResult({json.dumps(msg)}, 'err')")

        threading.Thread(target=work, daemon=True).start()

    def _load_yt_api_key(self):
        """Doc khoa API tu dia. Tra '' neu chua co hoac doc loi."""
        try:
            if not os.path.exists(self.yt_api_key_path):
                return ''
            with open(self.yt_api_key_path, 'r', encoding='utf-8') as f:
                return f.read().strip()
        except Exception:
            return ''

    def _load_claim_state(self):
        """Restore Claim Tiktok state (voiceDir, musicDir, outputDir, workers) from claim_state.json."""
        try:
            if not os.path.exists(self.claim_state_path):
                return
            with open(self.claim_state_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            voice = data.get('voiceDir', '')
            music = data.get('musicDir', '')
            output = data.get('outputDir', '')
            workers = data.get('workers', '')
            if voice:
                self._js(f"document.getElementById('voiceDir').value = {json.dumps(voice)}")
            if music:
                self._js(f"document.getElementById('musicDir').value = {json.dumps(music)}")
            if output:
                self._js(f"document.getElementById('outputDir').value = {json.dumps(output)}")
            if workers:
                self._js(f"document.getElementById('workers').value = {json.dumps(str(workers))}")
        except Exception as e:
            self._log(f"Loi tai claim state: {e}", 'err')

    # ── UI actions ──

    def onFuncChanged(self, func_name):
        code = self._code_map.get(func_name, {})
        code_type = code.get('type', '')
        self._js(f"uiApi.showGhepSection({str(code_type == 'concat').lower()})")
        self._js(f"uiApi.showSplitSection({str(code_type == 'split_video').lower()})")
        self._js(f"uiApi.showAudioSplitSection({str(code_type == 'split_audio').lower()})")
        self._js(f"uiApi.showConvertSection({str(code_type == 'convert_video').lower()})")
        self._js(f"uiApi.showOverlaySection({str(code_type == 'overlay_corner').lower()})")
        self._js(f"uiApi.showMultiFolderSection({str(code_type == 'concat_multi_folder').lower()})")
        self._js(f"uiApi.showClaimSection({str(code_type == 'claim_tiktok').lower()})")
        self._js(f"uiApi.showResizeSection({str(code_type == 'resize_image').lower()})")
        self._js(f"uiApi.showYoutubeSection({str(code_type == 'youtube_download').lower()})")
        self._js(f"uiApi.showYtThumbSection({str(code_type == 'youtube_thumbnail').lower()})")
        self._js(f"uiApi.showClaimJazzSection({str(code_type == 'claim_jazz').lower()})")

    def addSeparator(self):
        self._js("document.getElementById('editor').value += '#\\n'; updateLineCount();")

    def saveNoiTxt(self, text):
        with open(self.noi_txt_path, 'w', encoding='utf-8') as f:
            f.write(text)
        self._log("Da luu Noi.txt.", 'ok')
        self._js("uiApi.setStatus('Da luu Noi.txt.')")

    def clearEditor(self):
        self._js("document.getElementById('editor').value = ''; updateLineCount();")

    def browseInput(self):
        result = self._window.create_file_dialog(webview.FOLDER_DIALOG)
        if result and len(result) > 0:
            path = result[0]
            safe = path.replace('\\', '\\\\')
            self._js(f"uiApi.setInputDir('{safe}')")
            self.refreshVideos()

    def browseOutput(self):
        result = self._window.create_file_dialog(webview.FOLDER_DIALOG)
        if result and len(result) > 0:
            path = result[0]
            safe = path.replace('\\', '\\\\')
            self._js(f"uiApi.setOutputDir('{safe}')")

    def browseOverlayFile(self):
        result = self._window.create_file_dialog(webview.OPEN_DIALOG, file_types=('PNG files (*.png)',))
        if result and len(result) > 0:
            path = result[0]
            self._js(f"uiApi.setOverlayPath({json.dumps(path)})")

    def _browse_into(self, js_key):
        """Mo hop chon thu muc roi do duong dan vao mot o cua Claim Jazz."""
        result = self._window.create_file_dialog(webview.FOLDER_DIALOG)
        if result and len(result) > 0:
            self._js(f"uiApi.setJazzFolder('{js_key}', {json.dumps(result[0])})")

    def browseJazzGocFolder(self):
        self._browse_into('goc')

    def browseJazzNoiFolder(self):
        self._browse_into('noi')

    def browseJazzCidFolder(self):
        self._browse_into('cid')

    def browseKichBanFolder(self):
        result = self._window.create_file_dialog(webview.FOLDER_DIALOG)
        if result and len(result) > 0:
            path = result[0]
            self._js(f"uiApi.setMultiFolder('kichban', {json.dumps(path)})")

    def browseArtFolder(self):
        result = self._window.create_file_dialog(webview.FOLDER_DIALOG)
        if result and len(result) > 0:
            path = result[0]
            self._js(f"uiApi.setMultiFolder('art', {json.dumps(path)})")

    def browseEditFolder(self):
        result = self._window.create_file_dialog(webview.FOLDER_DIALOG)
        if result and len(result) > 0:
            path = result[0]
            self._js(f"uiApi.setMultiFolder('edit', {json.dumps(path)})")

    def browseVoiceDir(self):
        result = self._window.create_file_dialog(webview.FOLDER_DIALOG)
        if result and len(result) > 0:
            self._js(f"uiApi.setVoiceDir({json.dumps(result[0])})")

    def browseMusicDir(self):
        result = self._window.create_file_dialog(webview.FOLDER_DIALOG)
        if result and len(result) > 0:
            self._js(f"uiApi.setMusicDir({json.dumps(result[0])})")

    def ytThumbLoad(self):
        """Nut Load cua 'Tai thumbnail Youtube' (ADR-008). Khong tham so, tu
        doc cac truong bang evaluate_js - dung tien le refreshVideos() (function
        quet-roi-do-du-lieu-vao-panel-phai), khong mo rong pyApi proxy.

        LUU Y (sai lech voi khoi code vi du trong spec §4.1): spec khong doc
        '#workers' o day, nhung §6.8/§6.4/§6.5 deu can gia tri workers cho
        ThreadPoolExecutor + dong log "Tim thay {n} video | {workers} luong".
        Doc them '#workers' (giong moi function khac) va truyen xuong
        _yt_thumb_load_work la cach duy nhat de thuat toan spec mo ta chay
        dung - da bao lai trong report thay vi tu y bo qua workers.
        """
        if self.is_running:
            return                       # dung chung co voi RUN - khong chay chong
        links = self._window.evaluate_js("document.getElementById('ytThumbLinks').value") or ''
        size = self._window.evaluate_js("document.getElementById('ytThumbSize').value") or ''
        count = self._window.evaluate_js("document.getElementById('ytThumbCount').value") or ''
        workers_raw = self._window.evaluate_js("document.getElementById('workers').value") or ''
        func = self._window.evaluate_js("document.getElementById('funcSelect').value") or ''
        code = self._code_map.get(func, {})
        if code.get('type') != 'youtube_thumbnail':
            return
        try:
            workers = max(1, int(workers_raw))
        except (TypeError, ValueError):
            workers = 2
        self.is_running = True
        self._stopped = False
        self._paused = False
        self._current_procs.clear()
        self._js("uiApi.setRunning(true)")
        threading.Thread(target=self._yt_thumb_load_work,
                          args=(links, size, count, code, workers), daemon=True).start()

    def refreshVideos(self):
        input_dir = self._window.evaluate_js("document.getElementById('inputDir').value")
        if not input_dir:
            self._js("uiApi.setVideos([])")
            return
        os.makedirs(input_dir, exist_ok=True)
        video_exts = ('.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv', '.ts', '.m4v')
        videos = []
        for f in sorted(os.listdir(input_dir)):
            if f.lower().endswith(video_exts):
                fp = os.path.join(input_dir, f)
                try:
                    size = format_size(os.path.getsize(fp))
                except OSError:
                    size = "—"
                ext = os.path.splitext(f)[1].upper()
                videos.append({"name": f, "size": size, "ext": ext})
        data = json.dumps(videos)
        self._js(f"uiApi.setVideos({data})")
        self._log(f"Tim thay {len(videos)} video trong Input.", 'info')
        self._js(f"uiApi.setStatus('{len(videos)} video trong Input.')")

    def addSelectedToEditor(self, names):
        if not names:
            return
        text = '\\n'.join(names) + '\\n'
        self._js(f"document.getElementById('editor').value += '{text}'; updateLineCount();")

    # ── ADR-007: retry failed rows ──

    def _input_signature(self, params):
        """Chu ky dau vao: hash moi khoa trong params TRU RETRY_SIG_IGNORE.

        Dung chung cho moi function, ke ca function them sau nay - khong co
        bang anh xa nao phai bao tri (ADR-007, Nhom 4).
        'workers' bi loai: tang so luong roi chay lai phan loi la thao tac hop le.
        """
        try:
            data = {k: v for k, v in (params or {}).items()
                    if k not in self.RETRY_SIG_IGNORE}
            blob = json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)
        except Exception:
            blob = repr(params)
        return hashlib.sha1(blob.encode('utf-8', 'replace')).hexdigest()

    def _ask_retry_mode(self, params):
        """Tra ve 'all' | 'failed' | 'cancel'. Chay trong thread nen cua run().

        Khong hien popup (tra 'all' luon) khi:
          - chua co lan chay nao truoc do, HOAC
          - lan truoc khong co dong loi nao, HOAC
          - chu ky dau vao khac lan truoc (user da sua dau vao -> coi nhu chay moi)
        """
        sig = self._input_signature(params)

        if not self._retry_failed or self._retry_sig != sig:
            # Dau vao doi hoac khong co gi de chay lai -> bo trang thai cu
            self._retry_sig = sig
            self._retry_failed = []
            self._retry_total = 0
            return 'all'

        n_fail = len(self._retry_failed)
        n_all = self._retry_total or n_fail

        self._retry_choice = ''
        self._retry_event.clear()
        self._js(f"showRetryDialog({n_fail}, {n_all})")

        # Cho user chon. Khong dung sleep co dinh nhu tien le _check_already_encoded
        # vi dialog 3 nut khong chan luong JS nhu confirm().
        waited = 0.0
        while not self._retry_event.wait(0.2):
            waited += 0.2
            if self._stopped or waited >= 120.0:
                self._js("hideRetryDialog()")
                return 'cancel'
        return self._retry_choice or 'cancel'

    def retryChoice(self, choice):
        """Nhan lua chon tu popup. Goi tu JS: pywebview.api.retryChoice('failed').

        choice: 'failed' | 'all' | 'cancel'
        """
        self._retry_choice = choice if choice in ('failed', 'all', 'cancel') else 'cancel'
        self._retry_event.set()

    def _begin_run(self, mode, sig):
        """Chuan bi trang thai retry cho mot lan chay. Goi 1 lan tu run()."""
        self._retry_mode = mode
        self._retry_sig = sig
        self._retry_targets = set(self._retry_failed) if mode == 'failed' else None
        self._batch_outcomes = {}

    def _begin_batch(self, labels):
        """Dung process table va tra ve danh sach dong can chay lan nay.

        labels: list[str] - TOAN BO nhan (giong het tham so dang truyen cho
                uiApi.initProcessTable hien tai)
        return: list[(idx_goc, label)] - chi nhung dong can chay.
                idx_goc la index trong `labels`, dung cho updateProcessItem.

        Hop dong uiApi.initProcessTable KHONG doi: bang luon dung day du moi dong.
        Dong khong chay lai duoc to 'done' ngay (ADR-007, Quyet dinh 6).
        """
        labels = [str(x) for x in labels]
        self._batch_labels = labels
        self._batch_outcomes = {}
        self._js(f"uiApi.initProcessTable({json.dumps(labels)})")

        if self._retry_mode != 'failed' or self._retry_targets is None:
            return list(enumerate(labels))

        run_items, skipped = [], 0
        for i, lb in enumerate(labels):
            if lb in self._retry_targets:
                run_items.append((i, lb))
            else:
                skipped += 1
                self._batch_outcomes[lb] = True          # giu nguyen ket qua cu
                self._js(f"uiApi.updateProcessItem({i}, 100, 'done')")

        if not run_items:
            self._log("Khong con dong loi nao khop, chay lai tat ca.", 'info')
            self._batch_outcomes = {}
            return list(enumerate(labels))

        self._log(f"Chay lai {len(run_items)} dong loi (bo qua {skipped} dong da xong).", 'info')
        return run_items

    def _mark_row(self, idx, success):
        """Phat status cuoi cung cua mot dong + ghi ket qua theo nhan.

        Thay cho cap dong dang lap 12 lan:
            status = 'done' if success else 'error'
            self._js(f"uiApi.updateProcessItem({idx}, 100, '{status}')")
        Da gop luon nhanh 'stopped' von dang lap o _run_claim_tiktok + _run_youtube_download.
        """
        status = 'done' if success else 'error'
        if self._stopped:
            status = 'stopped'
        self._js(f"uiApi.updateProcessItem({idx}, 100, '{status}')")
        try:
            lb = self._batch_labels[idx]
        except (IndexError, AttributeError, TypeError):
            return                                   # khong co bang -> khong ghi gi
        if not self._stopped:
            self._batch_outcomes[lb] = bool(success)

    def _end_run(self):
        """Chot danh sach dong loi cho lan chay sau."""
        labels = self._batch_labels
        if not labels:
            return                       # handler khong co process table (hoac loi som) -> giu nguyen
        failed = [lb for lb in labels if self._batch_outcomes.get(lb) is False]
        # Dong chua co phan quyet (bi Stop / chua submit) coi nhu chua loi -> khong dua vao
        self._retry_failed = failed
        self._retry_total = len(labels)
        self._batch_labels = None

    # ── RUN ──

    def run(self, params):
        if self.is_running:
            return
        self.is_running = True
        self._stopped = False
        self._paused = False
        self._current_procs.clear()
        self._js("uiApi.setRunning(true)")

        func_name = params.get('func', '')
        code = self._code_map.get(func_name, {})
        code_type = code.get('type', '')

        def _work():
            try:
                mode = self._ask_retry_mode(params)
                if mode == 'cancel':
                    self._log("Da huy.", 'info')
                    self._js("uiApi.setStatus('Da huy.')")
                    return
                self._begin_run(mode, self._input_signature(params))

                if code_type == 'concat':
                    self._run_concat(params)
                elif code_type == 'convert_mp3':
                    self._run_convert(params)
                elif code_type == 'split_video':
                    self._run_split(params)
                elif code_type == 'split_audio':
                    self._run_audio_split(params, code)
                elif code_type == 'reencode':
                    self._run_reencode(params, code)
                elif code_type == 'convert_image':
                    self._run_convert_image(params, code)
                elif code_type == 'convert_audio':
                    self._run_convert_audio(params, code)
                elif code_type == 'strip_metadata':
                    self._run_strip_metadata(params, code)
                elif code_type == 'pad_duration':
                    self._run_pad_duration(params, code)
                elif code_type == 'convert_video':
                    self._run_convert_video(params, code)
                elif code_type == 'overlay_corner':
                    self._run_overlay_corner(params, code)
                elif code_type == 'concat_multi_folder':
                    self._run_concat_multi_folder(params, code)
                elif code_type == 'claim_tiktok':
                    self._run_claim_tiktok(params, code)
                elif code_type == 'claim_jazz':
                    self._run_claim_jazz(params, code)
                elif code_type == 'resize_image':
                    self._run_resize_image(params, code)
                elif code_type == 'youtube_download':
                    self._run_youtube_download(params, code)
                elif code_type == 'youtube_thumbnail':
                    self._run_yt_thumbnail(params, code)
                else:
                    self._log(f"Khong ho tro type: {code_type}", 'err')
            except Exception as e:
                self._log(f"LOI: {e}", 'err')
            finally:
                self._end_run()
                self.is_running = False
                self._js("uiApi.setRunning(false)")

        threading.Thread(target=_work, daemon=True).start()

    # ── Concat ──

    def _run_concat(self, params):
        self._js("uiApi.setStatus('Dang xu ly...')")
        self._js("uiApi.setProgress(0, '')")
        self._log("=== Bat dau ghep video ===", 'info')

        input_dir = params['inputDir']
        output_dir = params['outputDir']
        workers = max(1, params.get('workers', 2))

        if not input_dir or not output_dir:
            self._log("Chua chon folder Input hoac Output.", 'err')
            return
        if not os.path.exists(self.ffmpeg_path):
            self._log(f"Khong tim thay ffmpeg.exe", 'err')
            return

        os.makedirs(output_dir, exist_ok=True)

        text = params.get('editorText', '')
        lines = [l.strip() for l in text.split('\n')]
        groups, current = [], []
        for line in lines:
            if line == '#':
                if current: groups.append(current)
                current = []
            elif line:
                current.append(line)
        if current: groups.append(current)

        if not groups:
            self._log("Noi.txt rong hoac khong co nhom nao.", 'err')
            return

        total = len(groups)
        self._log(f"Tim thay {total} nhom | {workers} luong.", 'info')
        ok_count = [0]
        done_count = [0]

        def update(success):
            with self._lock:
                if success: ok_count[0] += 1
                done_count[0] += 1
                d = done_count[0]
            pct = int(d / total * 100)
            self._js(f"uiApi.setProgress({pct}, '{d}/{total}')")
            self._js(f"uiApi.setStatus('Dang xu ly... {d}/{total} nhom')")

        futures = {}
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for i, group in enumerate(groups, 1):
                f = ex.submit(self._process_group, i, group, total, input_dir, output_dir)
                futures[f] = i
            for f in as_completed(futures):
                try:
                    success, _ = f.result()
                except Exception as e:
                    success = False
                    self._log(f"  LOI: {e}", 'err')
                update(success)

        self._log(f"=== Hoan thanh: {ok_count[0]}/{total} nhom ===", 'ok')
        self._js(f"uiApi.setStatus('Xong! {ok_count[0]}/{total} nhom.')")

    def _process_group(self, i, group, total, input_dir, output_dir):
        names = [os.path.splitext(v)[0] for v in group]
        output_name = ', '.join(names) + '.mp4'
        output_path = os.path.join(output_dir, output_name)
        temp_list = os.path.join(self.bin_dir, f'_temp_list_{i}.txt')

        self._log(f"[{i}/{total}] Bat dau: {output_name}", 'info')

        with open(temp_list, 'w', encoding='utf-8') as f:
            for video in group:
                vpath = os.path.join(input_dir, video)
                f.write(f"file '{vpath}'\n")

        duration = sum(self._get_duration(os.path.join(input_dir, v)) for v in group)

        cmd = [self.ffmpeg_path, '-f', 'concat', '-safe', '0', '-i', temp_list,
               '-c', 'copy', '-progress', 'pipe:1', '-nostats', output_path, '-y']

        success = self._run_ffmpeg(cmd, i, total, duration, output_name)

        if os.path.exists(temp_list): os.remove(temp_list)
        return success, output_name

    # ── Convert MP3 ──

    def _run_convert(self, params):
        self._js("uiApi.setStatus('Dang convert MP3...')")
        self._js("uiApi.setProgress(0, '')")
        self._log("=== Bat dau convert MP4 -> MP3 ===", 'info')

        input_dir = params['inputDir']
        output_dir = params['outputDir']
        workers = max(1, params.get('workers', 2))

        if not input_dir or not output_dir:
            self._log("Chua chon folder Input hoac Output.", 'err')
            return

        os.makedirs(output_dir, exist_ok=True)
        mp4s = sorted(f for f in os.listdir(input_dir) if f.lower().endswith('.mp4'))
        if not mp4s:
            self._log("Khong tim thay file .mp4.", 'err')
            return

        run_items = self._begin_batch(mp4s)
        total = len(run_items)
        self._log(f"Tim thay {len(mp4s)} file | {workers} luong.", 'info')

        ok_count = [0]
        done_count = [0]

        def update(idx, success):
            with self._lock:
                if success: ok_count[0] += 1
                done_count[0] += 1
                d = done_count[0]
            self._mark_row(idx, success)
            self._js(f"uiApi.setProgress({int(d/total*100)}, '{d}/{total}')")
            self._js(f"uiApi.setStatus('Dang convert... {d}/{total} file')")

        def convert_one(idx, mp4):
            self._js(f"uiApi.updateProcessItem({idx}, 0, 'running')")
            mp3 = os.path.splitext(mp4)[0] + '.mp3'
            inp = os.path.join(input_dir, mp4)
            out = os.path.join(output_dir, mp3)
            dur = self._get_duration(inp)
            cmd = [self.ffmpeg_path, '-i', inp, '-vn', '-acodec', 'libmp3lame', '-q:a', '2',
                   '-progress', 'pipe:1', '-nostats', out, '-y']
            return self._run_ffmpeg_with_table(cmd, idx, dur, mp3)

        futures = {}
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for i, mp4 in run_items:
                futures[ex.submit(convert_one, i, mp4)] = i
            for f in as_completed(futures):
                idx = futures[f]
                try: success, _ = f.result()
                except: success = False
                update(idx, success)

        self._log(f"=== Hoan thanh: {ok_count[0]}/{total} file ===", 'ok')
        self._js(f"uiApi.setStatus('Xong! {ok_count[0]}/{total} file.')")

    # ── Split ──

    def _run_split(self, params):
        self._js("uiApi.setStatus('Dang chia nho video...')")
        self._js("uiApi.setProgress(0, '')")
        self._log("=== Bat dau chia nho video ===", 'info')

        input_dir = params['inputDir']
        output_dir = params['outputDir']
        workers = max(1, params.get('workers', 2))
        seg = max(1, params.get('splitSeconds', 300))

        if not input_dir or not output_dir:
            self._log("Chua chon folder.", 'err')
            return

        os.makedirs(output_dir, exist_ok=True)
        exts = ('.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv', '.ts', '.m4v')
        files = sorted(f for f in os.listdir(input_dir) if f.lower().endswith(exts))
        if not files:
            self._log("Khong tim thay video.", 'err')
            return

        total = len(files)
        self._log(f"Tim thay {total} video | Chia moi {seg} giay | {workers} luong.", 'info')
        ok_count = [0]
        done_count = [0]

        def update(success):
            with self._lock:
                if success: ok_count[0] += 1
                done_count[0] += 1
                d = done_count[0]
            self._js(f"uiApi.setProgress({int(d/total*100)}, '{d}/{total}')")

        def split_one(i, vf):
            name = os.path.splitext(vf)[0]
            ext = os.path.splitext(vf)[1]
            inp = os.path.join(input_dir, vf)
            pattern = os.path.join(output_dir, f"{name}_phan%03d{ext}")
            self._log(f"[{i}/{total}] Chia: {vf} ({seg}s)", 'info')
            cmd = [self.ffmpeg_path, '-i', inp, '-c', 'copy', '-f', 'segment',
                   '-segment_time', str(seg), '-reset_timestamps', '1', pattern, '-y']
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     text=True, creationflags=subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS)
            proc.communicate()
            ok = proc.returncode == 0
            if ok:
                parts = [f for f in os.listdir(output_dir) if f.startswith(f"{name}_phan")]
                self._log(f"  [{i}/{total}] OK: {len(parts)} phan", 'ok')
            else:
                self._log(f"  [{i}/{total}] LOI", 'err')
            return (ok, vf)

        futures = {}
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for i, vf in enumerate(files, 1):
                futures[ex.submit(split_one, i, vf)] = i
            for f in as_completed(futures):
                try: success, _ = f.result()
                except: success = False
                update(success)

        self._log(f"=== Hoan thanh: {ok_count[0]}/{total} video ===", 'ok')

    # ── Audio Split (ADR-009) ──

    @staticmethod
    def _audio_split_times(duration, n):
        """N-1 moc cat tuyet doi, chia deu 'duration' thanh n phan bang nhau.
        Tra list[float] tang dan, moi phan tu < duration.

        Co tinh truc tiep tai dur*k/N (KHONG chia trung binh roi lam tron):
        cach lam tron sai am tham vi du dur=100 parts=99 se ra 50 phan neu
        dung -segment_time ceil(dur/N) (ADR-009 Nhom 2).
        """
        return [round(duration * k / n, 3) for k in range(1, n)]

    def _ffmpeg_quiet(self, cmd):
        """Chay 1 lenh ffmpeg, KHONG log gi, KHONG parse tien trinh. Tra (ok, last_err).

        Dung cho nhanh FLAC cua _run_audio_split, noi MOT dong bang can N lan
        goi ffmpeg (moi phan mot lenh rieng) - _run_ffmpeg/_run_ffmpeg_with_table
        deu log co dinh moi lan goi nen se de lai N dong log rac + N lan ghi de
        % cua dong (ADR-009 Negative). Van dung DUNG pattern _current_procs de
        Pause/Stop hoat dong (Stop se kill proc dang chay qua _kill_all_ffmpeg,
        khong can vong lap kiem tra rieng trong ham nay).
        """
        if self._stopped:
            return False, 'Da dung.'

        proc = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            text=True, creationflags=subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS,
        )
        self._current_procs.append(proc)

        stderr_lines = []
        def drain():
            for line in proc.stderr:
                stderr_lines.append(line)
        t = threading.Thread(target=drain, daemon=True)
        t.start()

        proc.wait()
        t.join()

        if proc in self._current_procs:
            self._current_procs.remove(proc)

        if self._stopped:
            return False, 'Da dung.'

        if proc.returncode == 0:
            return True, ''

        err = ''.join(stderr_lines).strip()
        last_err = err.splitlines()[-1] if err else 'Unknown error'
        return False, last_err

    def _run_audio_split(self, params, code):
        self._js("uiApi.setStatus('Dang chia nho nhac...')")
        self._js("uiApi.setProgress(0, '')")
        self._log("=== Bat dau chia nho nhac ===", 'info')

        input_dir = params.get('inputDir', '')
        output_dir = params.get('outputDir', '')
        workers = max(1, params.get('workers', 2))
        from_exts = [e.lower() for e in code.get(
            'from_ext', ['.mp3', '.wav', '.m4a', '.flac', '.aac', '.ogg'])]
        reencode_exts = [e.lower() for e in code.get('reencode_ext', ['.flac'])]
        keep_cover = bool(code.get('keep_cover', False))
        min_part_seconds = float(code.get('min_part_seconds', 1.0))

        mode = (params.get('audioSplitMode') or code.get('default_mode', 'parts')).strip().lower()
        try:
            parts = int(params.get('audioSplitParts') or code.get('default_parts', 5))
        except (ValueError, TypeError):
            parts = 0
        try:
            seconds = int(params.get('audioSplitSeconds') or code.get('default_seconds', 300))
        except (ValueError, TypeError):
            seconds = 0

        if not input_dir or not output_dir:
            self._log("Chua chon folder.", 'err')
            return
        if not os.path.exists(self.ffmpeg_path):
            self._log("Khong tim thay ffmpeg.exe", 'err')
            return
        if mode not in ('parts', 'duration'):
            self._log(f"Che do chia khong hop le: {mode}", 'err')
            return
        if mode == 'parts' and parts < 2:
            self._log("So phan phai tu 2 tro len.", 'err')
            return
        if mode == 'duration' and seconds < 1:
            self._log("Thoi luong moi phan khong hop le.", 'err')
            return

        files = sorted(
            f for f in os.listdir(input_dir)
            if os.path.splitext(f)[1].lower() in from_exts
        )
        if not files:
            self._log("Khong tim thay file nhac trong Input.", 'err')
            return

        os.makedirs(output_dir, exist_ok=True)

        run_items = self._begin_batch(files)
        total = len(run_items)
        if mode == 'parts':
            self._log(f"Tim thay {len(files)} file | {workers} luong | chia {parts} phan.", 'info')
        else:
            self._log(f"Tim thay {len(files)} file | {workers} luong | chia moi {seconds} giay.", 'info')

        ok_count = [0]
        done_count = [0]

        def update(idx, success):
            with self._lock:
                if success: ok_count[0] += 1
                done_count[0] += 1
                d = done_count[0]
            self._mark_row(idx, success)
            self._js(f"uiApi.setProgress({int(d/total*100)}, '{d}/{total}')")
            self._js(f"uiApi.setStatus('Dang chia... {d}/{total} file')")

        def split_one(idx, filename):
            i = idx + 1
            self._js(f"uiApi.updateProcessItem({idx}, 0, 'running')")
            self._log(f"[{i}/{total}] Chia: {filename}", 'info')

            inp = os.path.join(input_dir, filename)
            stem, ext = os.path.splitext(filename)
            stem_safe = stem.replace('%', '_')
            use_branch_b = ext.lower() in reencode_exts

            rx = re.compile(r'^' + re.escape(stem_safe) + r' \((\d+)\)' + re.escape(ext) + r'$', re.IGNORECASE)
            old = [f2 for f2 in os.listdir(output_dir) if rx.match(f2)]
            if old:
                self._log(
                    f"[{i}/{total}] Da co {len(old)} file phan cu, se ghi de "
                    f"(phan thua khong bi xoa): {filename}", 'info')

            duration = self._get_duration(inp)
            fatal_zero_dur = use_branch_b or mode == 'parts'
            if duration <= 0 and fatal_zero_dur:
                self._log(f"[{i}/{total}] Khong doc duoc thoi luong: {filename}", 'err')
                return False, filename

            if mode == 'parts' and duration / parts < min_part_seconds:
                self._log(
                    f"[{i}/{total}] File qua ngan de chia {parts} phan: "
                    f"{filename} ({duration:.1f}s)", 'err')
                return False, filename

            pattern = os.path.join(output_dir, f"{stem_safe} (%d){ext}")

            if not use_branch_b:
                # NHANH A: mot lenh segment, khong ma hoa lai (WAV/MP3/M4A/AAC/OGG)
                cmd = [self.ffmpeg_path, '-i', inp]
                cmd += ['-map', '0' if keep_cover else '0:a']
                cmd += ['-c', 'copy', '-map_chapters', '-1']
                cmd += ['-f', 'segment']
                if mode == 'parts':
                    times = self._audio_split_times(duration, parts)
                    times_str = ','.join(f'{t:.3f}' for t in times)
                    cmd += ['-segment_times', times_str]
                else:
                    cmd += ['-segment_time', str(seconds)]
                cmd += ['-reset_timestamps', '1', '-segment_start_number', '1']
                cmd += ['-progress', 'pipe:1', '-nostats', pattern, '-y']

                success, _ = self._run_ffmpeg_with_table(cmd, idx, duration, filename)
            else:
                # NHANH B: chi FLAC - cat tung phan bang -ss/-t roi ma hoa lai
                # FLAC (lossless, MD5 trung khop tuyet doi - xem ADR-009 §7.4).
                # -ss/-t chi duoc dung cho dinh dang nay; lam duong chung se
                # hong .aac (0 KB) va .wav (0.1 KB) - da kiem chung, xem ADR-009 §7.3.
                if mode == 'parts':
                    starts = [0.0] + self._audio_split_times(duration, parts)
                else:
                    starts = []
                    t = 0.0
                    while t < duration - 0.001:
                        starts.append(t)
                        t += seconds
                n = len(starts)
                success = True
                for k in range(1, n + 1):
                    if self._stopped:
                        self._js(f"uiApi.updateProcessItem({idx}, {int((k-1)/n*100)}, 'stopped')")
                        success = False
                        break
                    start = starts[k - 1]
                    out_k = os.path.join(output_dir, f"{stem_safe} ({k}){ext}")
                    cmd_k = [self.ffmpeg_path, '-ss', f'{start:.3f}']
                    if k < n:                                   # phan cuoi KHONG co -t: doc toi EOF
                        cmd_k += ['-t', f'{starts[k] - start:.3f}']
                    cmd_k += ['-i', inp, '-map', '0:a', '-map_chapters', '-1', '-c:a', 'flac', out_k, '-y']
                    ok, last_err = self._ffmpeg_quiet(cmd_k)
                    if not ok:
                        self._log(f"[{i}/{total}] LOI phan {k}/{n} ({filename}): {last_err}", 'err')
                        success = False
                        break
                    self._js(f"uiApi.updateProcessItem({idx}, {int(k/n*100)}, 'running')")

            if success:
                k_actual = len([f2 for f2 in os.listdir(output_dir) if rx.match(f2)])
                self._log(f"  [{i}/{total}] OK: {k_actual} phan", 'ok')
            return success, filename

        futures = {}
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for i, f in run_items:
                if self._stopped: break
                futures[ex.submit(split_one, i, f)] = i
            for f in as_completed(futures):
                idx = futures[f]
                try:
                    success, _ = f.result()
                except Exception:
                    success = False
                update(idx, success)

        self._log(f"=== Hoan thanh: {ok_count[0]}/{total} file ===", 'ok')
        self._js(f"uiApi.setStatus('Xong! {ok_count[0]}/{total} file.')")

    # ── Re-encode ──

    def _cancel_reencode(self):
        self._stopped = True
        self._log("Da huy nen video.", 'info')
        self._js("uiApi.setStatus('Da huy.')")

    def _check_already_encoded(self, input_dir, files):
        """Check if videos are already compressed. Return list of warnings."""
        warnings = []
        for vf in files:
            fp = os.path.join(input_dir, vf)
            try:
                r = subprocess.run(
                    [self.ffprobe_path, '-v', 'quiet', '-print_format', 'json',
                     '-show_streams', '-show_format', fp],
                    capture_output=True, text=True,
                    creationflags=subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS
                )
                data = json.loads(r.stdout)
                fmt = data.get('format', {})
                streams = data.get('streams', [])

                # Check encoder tag (Lavf = FFmpeg encoded)
                encoder = fmt.get('tags', {}).get('encoder', '')
                is_ffmpeg = 'lavf' in encoder.lower() or 'lavc' in encoder.lower()

                # Check video stream
                for s in streams:
                    if s.get('codec_type') == 'video':
                        bitrate = int(s.get('bit_rate', 0))
                        codec = s.get('codec_name', '')
                        profile = s.get('profile', '')

                        # Low bitrate = already compressed
                        if bitrate > 0 and bitrate < 1500000:  # < 1.5 Mbps
                            warnings.append((vf, f"bitrate thap ({bitrate//1000}kbps) - da nen"))
                        # HEVC = already compressed with modern codec
                        elif codec == 'hevc' and is_ffmpeg:
                            warnings.append((vf, "da nen bang HEVC"))
                        # FFmpeg encoded + High profile = likely re-encoded
                        elif is_ffmpeg and profile == 'High':
                            warnings.append((vf, f"da encode boi FFmpeg ({bitrate//1000}kbps)"))
                        break
            except Exception:
                pass
        return warnings

    def _run_reencode(self, params, code):
        self._js("uiApi.setStatus('Dang nen video...')")
        self._js("uiApi.setProgress(0, '')")
        self._log(f"=== Bat dau: {code.get('name', 'Re-encode')} ===", 'info')

        input_dir = params['inputDir']
        output_dir = params['outputDir']
        workers = max(1, params.get('workers', 2))
        cmd_template = code.get('command', '')

        self._detect_gpu()

        if not input_dir or not output_dir:
            self._log("Chua chon folder.", 'err')
            return

        os.makedirs(output_dir, exist_ok=True)
        exts = ('.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv', '.ts', '.m4v')
        files = sorted(f for f in os.listdir(input_dir) if f.lower().endswith(exts))
        if not files:
            self._log("Khong tim thay video.", 'err')
            return

        # Check for already compressed videos
        warnings = self._check_already_encoded(input_dir, files)
        if warnings:
            self._log(f"⚠ Phat hien {len(warnings)} video co the da nen:", 'err')
            for vf, reason in warnings:
                self._log(f"  - {vf}: {reason}", 'err')
            self._log("Nen lai se giam chat luong. Tiep tuc...", 'info')
            # Show warning dialog
            warn_files = '\\n'.join([f"• {vf}: {r}" for vf, r in warnings[:5]])
            if len(warnings) > 5:
                warn_files += f"\\n... va {len(warnings)-5} file khac"
            self._js(f"if(!confirm('⚠ Phat hien {len(warnings)} video da nen:\\n\\n{warn_files}\\n\\nNen lai se giam chat luong!\\nBan co muon tiep tuc?')) {{ pywebview.api._cancel_reencode(); }}")
            # Small delay to let confirm show
            import time
            time.sleep(0.5)
            if self._stopped:
                return

        run_items = self._begin_batch(files)
        total = len(run_items)
        self._total_tasks = total
        self._task_results = {}
        self._log(f"Tim thay {len(files)} video | {workers} luong.", 'info')

        # Build task functions for each file
        def make_task(idx, vf):
            def task_fn():
                self._js(f"uiApi.updateProcessItem({idx}, 0, 'running')")
                inp = os.path.join(input_dir, vf)
                out = os.path.join(output_dir, vf)
                dur = self._get_duration(inp)

                parts = self._parse_cmd_template(cmd_template, inp, out)
                parts = self._swap_to_gpu(parts)
                cmd = [self.ffmpeg_path] + parts + ['-progress', 'pipe:1', '-nostats', '-y']
                return self._run_ffmpeg_with_table(cmd, idx, dur, vf)
            return task_fn

        all_tasks = [(i, make_task(i, vf)) for i, vf in run_items]
        self._pending_tasks = list(all_tasks)

        # Run batch
        self._run_pending_tasks(workers)

        # Wait for paused batches to finish (resume will restart)
        while self._pending_tasks and not self._stopped:
            import time
            time.sleep(0.5)

        ok = sum(1 for v in self._task_results.values() if v)
        self._log(f"=== Hoan thanh: {ok}/{total} video ===", 'ok')
        self._js(f"uiApi.setStatus('Xong! {ok}/{total} video.')")

    # ── Convert Image ──

    def _run_convert_image(self, params, code):
        self._js("uiApi.setStatus('Dang convert anh...')")
        self._js("uiApi.setProgress(0, '')")
        self._log(f"=== Bat dau: {code.get('name', 'Convert Image')} ===", 'info')

        input_dir = params['inputDir']
        output_dir = params['outputDir']
        workers = max(1, params.get('workers', 2))
        from_exts = [e.lower() for e in code.get('from_ext', [])]
        to_ext = code.get('to_ext', '.png')
        quality = code.get('quality', 95)

        if not input_dir or not output_dir:
            self._log("Chua chon folder.", 'err')
            return

        os.makedirs(output_dir, exist_ok=True)

        files = sorted(
            f for f in os.listdir(input_dir)
            if os.path.splitext(f)[1].lower() in from_exts
        )
        if not files:
            self._log(f"Khong tim thay file {', '.join(from_exts)} trong Input.", 'err')
            return

        run_items = self._begin_batch(files)
        total = len(run_items)
        self._log(f"Tim thay {len(files)} anh | {workers} luong.", 'info')

        ok_count = [0]
        done_count = [0]

        def update(idx, success):
            with self._lock:
                if success: ok_count[0] += 1
                done_count[0] += 1
                d = done_count[0]
            self._mark_row(idx, success)
            self._js(f"uiApi.setProgress({int(d/total*100)}, '{d}/{total}')")
            self._js(f"uiApi.setStatus('Dang convert... {d}/{total} anh')")

        def convert_one(idx, filename):
            self._js(f"uiApi.updateProcessItem({idx}, 0, 'running')")
            inp = os.path.join(input_dir, filename)
            new_name = os.path.splitext(filename)[0] + to_ext
            out = os.path.join(output_dir, new_name)
            try:
                img = Image.open(inp)
                if to_ext in ('.jpg', '.jpeg') and img.mode in ('RGBA', 'P'):
                    bg = Image.new('RGB', img.size, (255, 255, 255))
                    if img.mode == 'P':
                        img = img.convert('RGBA')
                    bg.paste(img, mask=img.split()[3])
                    img = bg
                elif img.mode == 'P':
                    img = img.convert('RGBA' if to_ext == '.png' else 'RGB')

                save_kwargs = {}
                if to_ext in ('.jpg', '.jpeg'):
                    save_kwargs = {'quality': quality, 'optimize': True}
                elif to_ext == '.webp':
                    save_kwargs = {'quality': quality, 'method': 4}
                elif to_ext == '.png':
                    save_kwargs = {'optimize': True}

                img.save(out, **save_kwargs)
                self._log(f"  OK: {new_name}", 'ok')
                return (True, new_name)
            except Exception as e:
                self._log(f"  LOI: {e}", 'err')
                return (False, new_name)

        futures = {}
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for i, f in run_items:
                futures[ex.submit(convert_one, i, f)] = i
            for f in as_completed(futures):
                idx = futures[f]
                try:
                    success, _ = f.result()
                except:
                    success = False
                update(idx, success)

        self._log(f"=== Hoan thanh: {ok_count[0]}/{total} anh ===", 'ok')
        self._js(f"uiApi.setStatus('Xong! {ok_count[0]}/{total} anh.')")

    # ── Resize Image ──

    def _run_resize_image(self, params, code):
        self._js("uiApi.setStatus('Dang resize anh...')")
        self._js("uiApi.setProgress(0, '')")
        self._log(f"=== Bat dau: {code.get('name', 'Resize Image')} ===", 'info')

        input_dir = params.get('inputDir', '')
        output_dir = params.get('outputDir', '')
        workers = max(1, params.get('workers', 2))
        from_exts = [e.lower() for e in code.get('from_ext', ['.jpg', '.jpeg', '.png', '.webp'])]

        if not input_dir or not output_dir:
            self._log("Chua chon folder.", 'err')
            return

        try:
            width = int(params.get('resizeWidth') or 0)
            height = int(params.get('resizeHeight') or 0)
        except (ValueError, TypeError):
            width, height = 0, 0

        if width <= 0 or height <= 0:
            self._log("Kich thuoc W x H khong hop le.", 'err')
            return

        os.makedirs(output_dir, exist_ok=True)

        files = sorted(
            f for f in os.listdir(input_dir)
            if os.path.splitext(f)[1].lower() in from_exts
        )
        if not files:
            self._log(f"Khong tim thay file {', '.join(from_exts)} trong Input.", 'err')
            return

        new_names = [os.path.splitext(f)[0] + '.jpg' for f in files]
        run_items = self._begin_batch(new_names)
        total = len(run_items)
        self._log(f"Tim thay {len(files)} anh | {workers} luong | resize {width}x{height} -> JPG 300 DPI.", 'info')

        ok_count = [0]
        done_count = [0]

        def update(idx, success):
            with self._lock:
                if success: ok_count[0] += 1
                done_count[0] += 1
                d = done_count[0]
            self._mark_row(idx, success)
            self._js(f"uiApi.setProgress({int(d/total*100)}, '{d}/{total}')")
            self._js(f"uiApi.setStatus('Dang resize... {d}/{total} anh')")

        def resize_one(idx, filename):
            self._js(f"uiApi.updateProcessItem({idx}, 0, 'running')")
            inp = os.path.join(input_dir, filename)
            new_name = os.path.splitext(filename)[0] + '.jpg'
            out = os.path.join(output_dir, new_name)
            try:
                img = Image.open(inp)
                # Output is always JPEG now -> flatten any alpha/palette onto white bg
                if img.mode in ('RGBA', 'P', 'LA'):
                    if img.mode != 'RGBA':
                        img = img.convert('RGBA')
                    bg = Image.new('RGB', img.size, (255, 255, 255))
                    bg.paste(img, mask=img.split()[-1])
                    img = bg
                elif img.mode != 'RGB':
                    img = img.convert('RGB')

                img = img.resize((width, height), Image.LANCZOS)

                img.save(out, 'JPEG', quality=95, optimize=True, dpi=(300, 300))
                self._log(f"  OK: {new_name}", 'ok')
                return (True, new_name)
            except Exception as e:
                self._log(f"  LOI: {e}", 'err')
                return (False, new_name)

        futures = {}
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for i, _lb in run_items:
                if self._stopped: break
                futures[ex.submit(resize_one, i, files[i])] = i
            for f in as_completed(futures):
                idx = futures[f]
                try:
                    success, _ = f.result()
                except Exception:
                    success = False
                update(idx, success)

        self._log(f"=== Hoan thanh: {ok_count[0]}/{total} anh ===", 'ok')
        self._js(f"uiApi.setStatus('Xong! {ok_count[0]}/{total} anh.')")

    # ── Overlay Corner ──

    def _run_overlay_corner(self, params, code):
        self._js("uiApi.setStatus('Dang ghep overlay...')")
        self._js("uiApi.setProgress(0, '')")
        self._log(f"=== Bat dau: {code.get('name', 'Overlay Corner')} ===", 'info')

        input_dir = params.get('inputDir', '')
        output_dir = params.get('outputDir', '')
        workers = max(1, params.get('workers', 2))
        overlay_path = params.get('overlayPath', '').strip()
        size = int(code.get('size', 65))
        margin = int(code.get('margin', 20))
        position = code.get('position', 'bottom-right')
        from_exts = [e.lower() for e in code.get('from_ext', ['.jpg', '.jpeg', '.png', '.webp'])]

        if not input_dir or not output_dir:
            self._log("Chua chon folder Input hoac Output.", 'err')
            return

        if not overlay_path:
            self._log("Chua chon file overlay PNG.", 'err')
            return

        if not os.path.isfile(overlay_path):
            self._log(f"Khong tim thay file overlay: {overlay_path}", 'err')
            return

        os.makedirs(output_dir, exist_ok=True)

        files = sorted(
            f for f in os.listdir(input_dir)
            if os.path.splitext(f)[1].lower() in from_exts
        )
        if not files:
            self._log(f"Khong tim thay file {', '.join(from_exts)} trong Input.", 'err')
            return

        # Open overlay once and resize — shared across all worker threads (read-only after resize)
        try:
            overlay_src = Image.open(overlay_path).convert('RGBA')
            overlay_src = overlay_src.resize((size, size), Image.LANCZOS)
        except Exception as e:
            self._log(f"Khong mo duoc file overlay: {e}", 'err')
            return

        run_items = self._begin_batch(files)
        total = len(run_items)
        self._log(f"Tim thay {len(files)} anh | overlay: {os.path.basename(overlay_path)} | {workers} luong.", 'info')

        ok_count = [0]
        done_count = [0]

        def update(idx, success):
            with self._lock:
                if success: ok_count[0] += 1
                done_count[0] += 1
                d = done_count[0]
            self._mark_row(idx, success)
            self._js(f"uiApi.setProgress({int(d / total * 100)}, '{d}/{total}')")
            self._js(f"uiApi.setStatus('Dang ghep overlay... {d}/{total} anh')")

        def composite_one(idx, filename):
            self._js(f"uiApi.updateProcessItem({idx}, 0, 'running')")
            inp = os.path.join(input_dir, filename)
            out = os.path.join(output_dir, filename)
            ext = os.path.splitext(filename)[1].lower()
            try:
                base = Image.open(inp).convert('RGBA')
                w, h = base.size

                # Compute paste position
                if position == 'bottom-right':
                    paste_x = w - size - margin
                    paste_y = h - size - margin
                elif position == 'bottom-left':
                    paste_x = margin
                    paste_y = h - size - margin
                elif position == 'top-right':
                    paste_x = w - size - margin
                    paste_y = margin
                elif position == 'top-left':
                    paste_x = margin
                    paste_y = margin
                else:
                    paste_x = w - size - margin
                    paste_y = h - size - margin

                # Clamp so the overlay never extends outside the image
                paste_x = max(0, paste_x)
                paste_y = max(0, paste_y)

                base.paste(overlay_src, (paste_x, paste_y), overlay_src)

                save_kwargs = {}
                if ext in ('.jpg', '.jpeg'):
                    # JPEG does not support alpha — flatten onto white background
                    flat = Image.new('RGB', base.size, (255, 255, 255))
                    flat.paste(base, mask=base.split()[3])
                    flat.save(out, 'JPEG', quality=95, optimize=True)
                elif ext == '.png':
                    base.save(out, 'PNG', optimize=True)
                elif ext == '.webp':
                    base.save(out, 'WEBP', quality=95, method=4)
                else:
                    base.convert('RGB').save(out)

                self._log(f"  OK: {filename}", 'ok')
                return (True, filename)
            except Exception as e:
                self._log(f"  LOI: {filename}: {e}", 'err')
                return (False, filename)

        futures = {}
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for i, f in run_items:
                if self._stopped:
                    break
                futures[ex.submit(composite_one, i, f)] = i
            for fut in as_completed(futures):
                idx = futures[fut]
                try:
                    success, _ = fut.result()
                except Exception:
                    success = False
                update(idx, success)

        self._log(f"=== Hoan thanh: {ok_count[0]}/{total} anh ===", 'ok')
        self._js(f"uiApi.setStatus('Xong! {ok_count[0]}/{total} anh.')")

    # ── Concat Multi-Folder ──

    def _run_concat_multi_folder(self, params, code):
        self._js("uiApi.setStatus('Dang ghep 4 folder...')")
        self._js("uiApi.setProgress(0, '')")
        self._log("=== Bat dau Ghep 4 Folder ===", 'info')

        folders = params.get('folders', {})
        goc_dir    = params.get('inputDir', '').strip()
        kichban_dir = folders.get('kichban', '').strip()
        art_dir    = folders.get('art', '').strip()
        edit_dir   = folders.get('edit', '').strip()
        output_dir = params.get('outputDir', '').strip()
        workers    = max(1, params.get('workers', 2))

        # ── Validate folder paths ──
        folder_labels = [
            ('Input (Video goc)', goc_dir),
            ('Kich Ban', kichban_dir),
            ('Art', art_dir),
            ('Edit', edit_dir),
            ('Output', output_dir),
        ]
        for label, path in folder_labels:
            if not path:
                self._log(f"Chua chon folder {label}.", 'err')
                return
            if label != 'Output' and not os.path.isdir(path):
                self._log(f"Khong tim thay folder {label}: {path}", 'err')
                return

        if not os.path.exists(self.ffmpeg_path):
            self._log("Khong tim thay ffmpeg.exe", 'err')
            return

        os.makedirs(output_dir, exist_ok=True)

        # ── Scan each folder ──
        extensions = tuple(e.lower() for e in code.get('extensions', [
            '.mp4', '.mkv', '.mov', '.avi', '.ts', '.m4v', '.wmv', '.flv'
        ]))

        def scan_folder(path, label):
            files = sorted(
                f for f in os.listdir(path)
                if os.path.splitext(f)[1].lower() in extensions
            )
            return files

        goc_list    = scan_folder(goc_dir, 'Video goc')
        kichban_list = scan_folder(kichban_dir, 'Kich Ban')
        art_list    = scan_folder(art_dir, 'Art')
        edit_list   = scan_folder(edit_dir, 'Edit')

        for label, lst, path in [
            ('Video goc', goc_list, goc_dir),
            ('Kich Ban', kichban_list, kichban_dir),
            ('Art', art_list, art_dir),
            ('Edit', edit_list, edit_dir),
        ]:
            if not lst:
                self._log(f"Folder {label} rong, dung.", 'err')
                return

        run_items = self._begin_batch(goc_list)
        total = len(run_items)
        self._log(
            f"Tim thay {len(goc_list)} video goc | "
            f"Kich Ban: {len(kichban_list)} | Art: {len(art_list)} | Edit: {len(edit_list)} | "
            f"{workers} luong.",
            'info'
        )

        ok_count = [0]
        done_count = [0]

        def update(idx, success):
            with self._lock:
                if success: ok_count[0] += 1
                done_count[0] += 1
                d = done_count[0]
            self._mark_row(idx, success)
            self._js(f"uiApi.setProgress({int(d / total * 100)}, '{d}/{total}')")
            self._js(f"uiApi.setStatus('Dang ghep... {d}/{total} video')")

        def process_one(idx, goc_filename):
            self._js(f"uiApi.updateProcessItem({idx}, 0, 'running')")

            # Pick random auxiliary videos (with replacement)
            kb_pick  = random.choice(kichban_list)
            art_pick = random.choice(art_list)
            edit_pick = random.choice(edit_list)

            self._log(
                f"[{idx + 1}/{total}] {goc_filename} + {kb_pick} + {art_pick} + {edit_pick}",
                'info'
            )

            # Build 4-file concat list in a temp file unique to this worker
            temp_list = os.path.join(
                self.bin_dir, f'_temp_multi_{idx}_{uuid.uuid4().hex[:8]}.txt'
            )
            entries = [
                (goc_dir,    goc_filename),
                (kichban_dir, kb_pick),
                (art_dir,    art_pick),
                (edit_dir,   edit_pick),
            ]
            try:
                with open(temp_list, 'w', encoding='utf-8') as fh:
                    for folder, fname in entries:
                        fpath = os.path.join(folder, fname)
                        fh.write(f"file '{fpath}'\n")

                duration = sum(
                    self._get_duration(os.path.join(folder, fname))
                    for folder, fname in entries
                )

                # ── Spec-mismatch detection ──
                specs = [
                    self._probe_spec(os.path.join(folder, fname))
                    for folder, fname in entries
                ]

                use_copy = True
                diff_fields = []

                if any(s is None for s in specs):
                    use_copy = False
                    diff_fields.append('probe failure')
                else:
                    video_keys = ('v_codec', 'width', 'height', 'fps', 'pix_fmt')
                    audio_keys = ('a_codec', 'sample_rate', 'channels')
                    check_keys = video_keys + audio_keys

                    for key in check_keys:
                        values = [s[key] for s in specs]
                        if len(set(str(v) for v in values)) > 1:
                            use_copy = False
                            # Map internal key name to a readable label
                            label_map = {
                                'v_codec': 'video codec',
                                'width': 'width',
                                'height': 'height',
                                'fps': 'frame rate',
                                'pix_fmt': 'pixel format',
                                'a_codec': 'audio codec',
                                'sample_rate': 'audio sample rate',
                                'channels': 'audio channels',
                            }
                            diff_fields.append(label_map.get(key, key))

                output_path = os.path.join(output_dir, goc_filename)

                if use_copy:
                    self._log(
                        f"  [{idx + 1}/{total}] {goc_filename}: video stream-copy + audio re-encode AAC (cung spec)",
                        'info'
                    )
                    cmd = [
                        self.ffmpeg_path,
                        '-f', 'concat', '-safe', '0',
                        '-i', temp_list,
                        '-c:v', 'copy',
                        '-c:a', 'aac', '-b:a', '192k', '-ac', '2', '-ar', '48000',
                        '-progress', 'pipe:1', '-nostats',
                        output_path, '-y',
                    ]
                else:
                    diff_summary = ' / '.join(diff_fields) if diff_fields else 'unknown'
                    # Use concat FILTER (not demuxer): decode -> scale/resample -> re-encode.
                    # Concat demuxer mangles audio DTS across mismatched sample rates,
                    # causing silent gaps. Filter decodes & normalizes everything first.
                    goc_spec = specs[0] if specs and specs[0] else None
                    target_w   = goc_spec['width']  if goc_spec else 1920
                    target_h   = goc_spec['height'] if goc_spec else 1080
                    target_fps = goc_spec['fps']    if goc_spec else '30000/1001'

                    filter_parts = []
                    for i in range(4):
                        filter_parts.append(
                            f"[{i}:v]scale={target_w}:{target_h}:force_original_aspect_ratio=decrease,"
                            f"pad={target_w}:{target_h}:-1:-1:color=black,setsar=1,fps={target_fps}[v{i}]"
                        )
                    for i in range(4):
                        filter_parts.append(f"[{i}:a]aresample=48000:async=1[a{i}]")
                    concat_pins = ''.join(f"[v{i}][a{i}]" for i in range(4))
                    filter_parts.append(f"{concat_pins}concat=n=4:v=1:a=1[v][a]")
                    filter_complex = ';'.join(filter_parts)

                    gpu = self._detect_gpu()
                    if gpu == 'nvenc':
                        venc = ['-c:v', 'h264_nvenc', '-preset', 'fast', '-cq', '20']
                        encoder_tag = 'GPU h264_nvenc'
                    elif gpu == 'amf':
                        venc = ['-c:v', 'h264_amf', '-quality', 'speed', '-qp_i', '20', '-qp_p', '22']
                        encoder_tag = 'GPU h264_amf'
                    elif gpu == 'qsv':
                        venc = ['-c:v', 'h264_qsv', '-preset', 'fast', '-global_quality', '20']
                        encoder_tag = 'GPU h264_qsv'
                    else:
                        venc = ['-c:v', 'libx264', '-preset', 'medium', '-crf', '20']
                        encoder_tag = 'CPU libx264'

                    cmd = [self.ffmpeg_path]
                    for folder, fname in entries:
                        cmd += ['-i', os.path.join(folder, fname)]
                    cmd += [
                        '-filter_complex', filter_complex,
                        '-map', '[v]', '-map', '[a]',
                        *venc,
                        '-pix_fmt', 'yuv420p',
                        '-c:a', 'aac', '-b:a', '192k', '-ac', '2', '-ar', '48000',
                        '-progress', 'pipe:1', '-nostats',
                        output_path, '-y',
                    ]
                    self._log(
                        f"  [{idx + 1}/{total}] {goc_filename}: re-encode {encoder_tag} (khac spec: {diff_summary})",
                        'info'
                    )

                success, _ = self._run_ffmpeg_with_table(cmd, idx, duration, goc_filename)
                return success, goc_filename
            finally:
                if os.path.exists(temp_list):
                    os.remove(temp_list)

        futures = {}
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for i, goc_filename in run_items:
                if self._stopped:
                    break
                futures[ex.submit(process_one, i, goc_filename)] = i
            for fut in as_completed(futures):
                idx = futures[fut]
                try:
                    success, _ = fut.result()
                except Exception as e:
                    success = False
                    self._log(f"  LOI: {e}", 'err')
                update(idx, success)

        self._log(f"=== Hoan thanh: {ok_count[0]}/{total} video ===", 'ok')
        self._js(f"uiApi.setStatus('Xong! {ok_count[0]}/{total} video.')")

    # ── Convert Audio ──

    def _run_convert_audio(self, params, code):
        self._js("uiApi.setStatus('Dang convert audio...')")
        self._js("uiApi.setProgress(0, '')")
        self._log(f"=== Bat dau: {code.get('name', 'Convert Audio')} ===", 'info')

        input_dir = params['inputDir']
        output_dir = params['outputDir']
        workers = max(1, params.get('workers', 2))
        from_exts = [e.lower() for e in code.get('from_ext', [])]
        to_ext = code.get('to_ext', '.mp3')

        if not input_dir or not output_dir:
            self._log("Chua chon folder.", 'err')
            return

        os.makedirs(output_dir, exist_ok=True)
        files = sorted(
            f for f in os.listdir(input_dir)
            if os.path.splitext(f)[1].lower() in from_exts
        )
        if not files:
            self._log(f"Khong tim thay file {', '.join(from_exts)} trong Input.", 'err')
            return

        run_items = self._begin_batch(files)
        total = len(run_items)
        self._log(f"Tim thay {len(files)} file | {workers} luong.", 'info')

        ok_count = [0]
        done_count = [0]

        def update(idx, success):
            with self._lock:
                if success: ok_count[0] += 1
                done_count[0] += 1
                d = done_count[0]
            self._mark_row(idx, success)
            self._js(f"uiApi.setProgress({int(d/total*100)}, '{d}/{total}')")
            self._js(f"uiApi.setStatus('Dang convert... {d}/{total} file')")

        # Encoder args keyed by output extension.
        # Fallback (unknown ext): pass only -vn and let FFmpeg infer the codec
        # from the output filename — safer than guessing or failing silently.
        _AUDIO_ENCODER_ARGS = {
            '.mp3': ['-vn', '-acodec', 'libmp3lame', '-q:a', '2'],
            '.wav': ['-vn', '-acodec', 'pcm_s16le'],
        }
        encoder_args = _AUDIO_ENCODER_ARGS.get(
            to_ext.lower(),
            ['-vn'],
        )
        if to_ext.lower() not in _AUDIO_ENCODER_ARGS:
            self._log(
                f"Canh bao: khong co encoder mapping cho '{to_ext}', "
                "de FFmpeg tu suy ra codec tu extension.",
                'info',
            )

        def convert_one(idx, filename):
            self._js(f"uiApi.updateProcessItem({idx}, 0, 'running')")
            inp = os.path.join(input_dir, filename)
            new_name = os.path.splitext(filename)[0] + to_ext
            out = os.path.join(output_dir, new_name)
            dur = self._get_duration(inp)
            cmd = [self.ffmpeg_path, '-i', inp] + encoder_args + [
                '-progress', 'pipe:1', '-nostats', out, '-y',
            ]
            return self._run_ffmpeg_with_table(cmd, idx, dur, new_name)

        futures = {}
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for i, f in run_items:
                futures[ex.submit(convert_one, i, f)] = i
            for f in as_completed(futures):
                idx = futures[f]
                try:
                    success, _ = f.result()
                except:
                    success = False
                update(idx, success)

        self._log(f"=== Hoan thanh: {ok_count[0]}/{total} file ===", 'ok')
        self._js(f"uiApi.setStatus('Xong! {ok_count[0]}/{total} file.')")

    # ── Strip Metadata ──

    def _run_strip_metadata(self, params, code):
        mode = code.get('mode', 'video')
        self._js(f"uiApi.setStatus('Dang xoa metadata ({mode})...')")
        self._js("uiApi.setProgress(0, '')")
        self._log(f"=== Bat dau: {code.get('name', 'Strip metadata')} ===", 'info')

        # Probe GPU once. Will only effective when preset's command actually re-encodes (libx264/libx265).
        gpu = self._detect_gpu() if mode == 'video' else None
        if mode == 'video':
            if gpu:
                self._log(f"GPU encoder: {gpu.upper()} (uu tien neu re-encode)", 'ok')
            else:
                self._log("Khong co GPU encoder, dung CPU.", 'info')

        input_dir = params['inputDir']
        output_dir = params['outputDir']
        workers = max(1, params.get('workers', 2))

        if not input_dir or not output_dir:
            self._log("Chua chon folder.", 'err')
            return
        os.makedirs(output_dir, exist_ok=True)

        if mode == 'image':
            default_exts = ['.jpg', '.jpeg', '.png', '.webp']
        elif mode == 'audio':
            default_exts = ['.mp3', '.m4a', '.wav', '.flac', '.aac', '.ogg']
        else:
            default_exts = ['.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv', '.ts', '.m4v']
        from_exts = [e.lower() for e in code.get('from_ext', default_exts)]

        files = sorted(
            f for f in os.listdir(input_dir)
            if os.path.splitext(f)[1].lower() in from_exts
        )
        if not files:
            self._log(f"Khong tim thay file {', '.join(from_exts)} trong Input.", 'err')
            return

        run_items = self._begin_batch(files)
        total = len(run_items)
        self._log(f"Tim thay {len(files)} file | {workers} luong.", 'info')

        ok_count = [0]
        done_count = [0]

        def update(idx, success):
            with self._lock:
                if success: ok_count[0] += 1
                done_count[0] += 1
                d = done_count[0]
            self._mark_row(idx, success)
            self._js(f"uiApi.setProgress({int(d/total*100)}, '{d}/{total}')")
            self._js(f"uiApi.setStatus('Dang xoa metadata... {d}/{total}')")

        def strip_image(idx, filename):
            self._js(f"uiApi.updateProcessItem({idx}, 0, 'running')")
            inp = os.path.join(input_dir, filename)
            out = os.path.join(output_dir, filename)
            try:
                img = Image.open(inp)
                # Rebuild via putdata to drop EXIF/ICC/XMP/C2PA — save(exif=None) alone doesn't strip all chunks
                clean = Image.new(img.mode, img.size)
                clean.putdata(list(img.getdata()))
                ext = os.path.splitext(filename)[1].lower()
                if ext in ('.jpg', '.jpeg'):
                    clean.save(out, 'JPEG', quality=95, optimize=True)
                elif ext == '.png':
                    clean.save(out, 'PNG', optimize=True)
                elif ext == '.webp':
                    clean.save(out, 'WEBP', quality=95, method=6)
                else:
                    clean.save(out)
                img.close()
                self._log(f"  OK: {filename}", 'ok')
                return True
            except Exception as e:
                self._log(f"  LOI {filename}: {e}", 'err')
                return False

        def strip_ffmpeg(idx, filename):
            self._js(f"uiApi.updateProcessItem({idx}, 0, 'running')")
            inp = os.path.join(input_dir, filename)
            out = os.path.join(output_dir, filename)
            dur = self._get_duration(inp) if mode == 'video' else 0
            cmd_template = code.get('command', '')
            parts = self._parse_cmd_template(cmd_template, inp, out)
            if gpu:
                parts = self._swap_to_gpu(parts)
            cmd = [self.ffmpeg_path] + parts + ['-progress', 'pipe:1', '-nostats', '-y']
            success, _ = self._run_ffmpeg_with_table(cmd, idx, dur, filename)
            return success

        worker_fn = strip_image if mode == 'image' else strip_ffmpeg

        futures = {}
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for i, f in run_items:
                if self._stopped: break
                futures[ex.submit(worker_fn, i, f)] = i
            for fut in as_completed(futures):
                idx = futures[fut]
                try:
                    success = fut.result()
                except Exception:
                    success = False
                update(idx, success)

        self._log(f"=== Hoan thanh: {ok_count[0]}/{total} file ===", 'ok')
        self._js(f"uiApi.setStatus('Xong! {ok_count[0]}/{total} file.')")

    # ── Convert Video ──

    # 'vcodec' = ten codec ma ffprobe se bao cao cho file SAU khi convert. Dung
    # de bo qua file vao von da dung codec do (xem _run_convert_video). Luu y no
    # KHONG doi khi swap sang GPU: h264_nvenc/h264_amf/h264_qsv deu ra 'h264'.
    CONVERT_TARGETS = {
        'MP4':  {'ext': '.mp4',  'vcodec': 'h264',  'args': ['-c:v', 'libx264', '-preset', 'medium', '-crf', '20', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart']},
        'MOV':  {'ext': '.mov',  'vcodec': 'h264',  'args': ['-c:v', 'libx264', '-preset', 'medium', '-crf', '20', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '192k']},
        'MKV':  {'ext': '.mkv',  'vcodec': 'h264',  'args': ['-c:v', 'libx264', '-preset', 'medium', '-crf', '20', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '192k']},
        'WEBM': {'ext': '.webm', 'vcodec': 'vp9',   'args': ['-c:v', 'libvpx-vp9', '-crf', '30', '-b:v', '0', '-deadline', 'good', '-cpu-used', '4', '-c:a', 'libopus', '-b:a', '128k']},
        'AVI':  {'ext': '.avi',  'vcodec': 'mpeg4', 'args': ['-c:v', 'mpeg4', '-vtag', 'XVID', '-q:v', '5', '-c:a', 'libmp3lame', '-q:a', '4']},
        'FLV':  {'ext': '.flv',  'vcodec': 'h264',  'args': ['-c:v', 'libx264', '-preset', 'medium', '-crf', '23', '-c:a', 'aac', '-b:a', '128k', '-ar', '44100']},
        'WMV':  {'ext': '.wmv',  'vcodec': 'wmv2',  'args': ['-c:v', 'wmv2', '-b:v', '4M', '-c:a', 'wmav2', '-b:a', '192k']},
    }

    CONVERT_SOURCE_EXTS = ['.mp4', '.mov', '.mkv', '.avi', '.flv', '.webm', '.wmv',
                           '.ts', '.mpg', '.mpeg', '.m2v', '.vob', '.m4v',
                           '.3gp', '.3gpp', '.mxf', '.hevc', '.h265', '.f4v']

    def _run_convert_video(self, params, code):
        target = (params.get('convertTarget') or 'MP4').upper()
        spec = self.CONVERT_TARGETS.get(target)
        if not spec:
            self._log(f"Target khong ho tro: {target}", 'err')
            return
        to_ext = spec['ext']
        want_vcodec = spec.get('vcodec')
        # File dung duoi dich VA dung codec dich thi khong con gi de lam - ma
        # hoa lai no chi ton hang gio va lam giam chat luong them mot doi. Chi
        # co tac dung voi file trung duoi dich (xem convert_one), nen dat mac
        # dinh bat khong lam doi hanh vi cu.
        skip_same_codec = bool(code.get('skip_same_codec', True))

        self._js(f"uiApi.setStatus('Dang convert video sang {target}...')")
        self._js("uiApi.setProgress(0, '')")
        self._log(f"=== Bat dau: Convert video sang {target} ===", 'info')

        # Try GPU only for H.264-based targets (MP4/MOV/MKV/FLV). WEBM/AVI/WMV use codec không có GPU equivalent.
        use_gpu = target in ('MP4', 'MOV', 'MKV', 'FLV')
        if use_gpu:
            gpu = self._detect_gpu()
            if gpu:
                self._log(f"GPU encoder: {gpu.upper()} (uu tien)", 'ok')
            else:
                self._log("Khong co GPU encoder, dung CPU.", 'info')
        else:
            self._log(f"Target {target} dung codec khong co GPU equivalent, dung CPU.", 'info')

        input_dir = params['inputDir']
        output_dir = params['outputDir']
        workers = max(1, params.get('workers', 2))

        if not input_dir or not output_dir:
            self._log("Chua chon folder.", 'err')
            return
        os.makedirs(output_dir, exist_ok=True)

        # File trung duoi dich CO duoc nhan lam nguon. Truoc day chung bi loai
        # thang, khien "MP4 chua VP9 -> MP4 chua H.264" thanh viec app khong lam
        # duoc - dung thu can den sau khi YouTube tra ve VP9 o muc Best (ADR-016).
        # Doi lai phai tu chan ghi de, xem ngay ben duoi.
        files = sorted(
            f for f in os.listdir(input_dir)
            if os.path.splitext(f)[1].lower() in self.CONVERT_SOURCE_EXTS
        )

        # Cung mot thu muc thi file trung duoi dich se co duong dan ra TRUNG
        # duong dan vao -> ffmpeg vua doc vua ghi mot file -> hong file goc.
        # Loai chung ra thay vi bao loi ca me: bao loi se pha hanh vi cu cua
        # nguoi dang convert AVI->MP4 ngay trong mot thu muc, thu van an toan.
        if (os.path.normcase(os.path.abspath(input_dir))
                == os.path.normcase(os.path.abspath(output_dir))):
            clash = [f for f in files
                     if os.path.splitext(f)[1].lower() == to_ext]
            if clash:
                files = [f for f in files if f not in set(clash)]
                self._log(
                    f"Input va Output la cung mot thu muc nen bo qua {len(clash)}"
                    f" file *{to_ext} (convert tai cho se ghi de len chinh file"
                    f" goc). Chon thu muc Output khac neu muon xu ly chung.",
                    'info')

        if not files:
            self._log("Khong tim thay video nguon.", 'err')
            return

        run_items = self._begin_batch(files)
        total = len(run_items)
        self._log(f"Tim thay {len(files)} video | {workers} luong | -> {target}.", 'info')

        ok_count = [0]
        done_count = [0]

        def update(idx, success):
            with self._lock:
                if success: ok_count[0] += 1
                done_count[0] += 1
                d = done_count[0]
            self._mark_row(idx, success)
            self._js(f"uiApi.setProgress({int(d/total*100)}, '{d}/{total}')")
            self._js(f"uiApi.setStatus('Dang convert sang {target}... {d}/{total}')")

        def convert_one(idx, filename):
            self._js(f"uiApi.updateProcessItem({idx}, 0, 'running')")
            inp = os.path.join(input_dir, filename)
            new_name = os.path.splitext(filename)[0] + to_ext
            out = os.path.join(output_dir, new_name)

            # Chi kiem file TRUNG duoi dich - dung nhom vua duoc mo them o tren.
            # File khac duoi thi luon phai chuyen, khong ton mot lan probe nao,
            # nen hanh vi cu khong doi mot ly. Probe nam trong worker (khong
            # phai mot vong quet truoc) de N lan goi ffprobe chay song song.
            # probe fail -> probed None -> van chuyen; hong ve phia an toan.
            if (skip_same_codec and want_vcodec
                    and os.path.splitext(filename)[1].lower() == to_ext):
                probed = self._probe_spec(inp)
                if probed and probed.get('v_codec') == want_vcodec:
                    self._log(f"  [{idx + 1}] Da la {want_vcodec} san, bo qua:"
                              f" {filename}", 'info')
                    return True

            dur = self._get_duration(inp)
            parts = ['-i', inp] + spec['args'] + [out]
            if use_gpu:
                parts = self._swap_to_gpu(parts)
            cmd = [self.ffmpeg_path] + parts + ['-progress', 'pipe:1', '-nostats', '-y']
            success, _ = self._run_ffmpeg_with_table(cmd, idx, dur, new_name)
            return success

        futures = {}
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for i, f in run_items:
                if self._stopped: break
                futures[ex.submit(convert_one, i, f)] = i
            for fut in as_completed(futures):
                idx = futures[fut]
                try:
                    success = fut.result()
                except Exception:
                    success = False
                update(idx, success)

        self._log(f"=== Hoan thanh: {ok_count[0]}/{total} video -> {target} ===", 'ok')
        self._js(f"uiApi.setStatus('Xong! {ok_count[0]}/{total} video -> {target}.')")

    # ── Pad Duration ──

    def _has_audio_stream(self, path):
        try:
            r = subprocess.run(
                [self.ffprobe_path, '-v', 'error', '-select_streams', 'a',
                 '-show_entries', 'stream=codec_type', '-of', 'csv=p=0', path],
                capture_output=True, text=True,
                creationflags=subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS
            )
            return 'audio' in (r.stdout or '')
        except Exception:
            return False

    def _probe_video_spec(self, path):
        try:
            r = subprocess.run(
                [self.ffprobe_path, '-v', 'error', '-select_streams', 'v:0',
                 '-show_entries', 'stream=width,height,r_frame_rate',
                 '-of', 'default=noprint_wrappers=1:nokey=1', path],
                capture_output=True, text=True,
                creationflags=subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS
            )
            lines = [l.strip() for l in (r.stdout or '').strip().split('\n') if l.strip()]
            if len(lines) >= 3:
                return {'width': int(lines[0]), 'height': int(lines[1]), 'fps': lines[2]}
        except Exception:
            pass
        return {'width': 1920, 'height': 1080, 'fps': '30'}

    def _run_pad_duration(self, params, code):
        target = int(code.get('target_seconds', 162000))
        self._js(f"uiApi.setStatus('Dang keo dai video len {target}s...')")
        self._js("uiApi.setProgress(0, '')")
        self._log(f"=== Bat dau: {code.get('name', 'Pad duration')} ===", 'info')

        gpu = self._detect_gpu()
        if gpu:
            self._log(f"GPU encoder: {gpu.upper()} (uu tien)", 'ok')
        else:
            self._log("Khong co GPU encoder, dung CPU.", 'info')

        input_dir = params['inputDir']
        output_dir = params['outputDir']
        workers = max(1, params.get('workers', 2))

        if not input_dir or not output_dir:
            self._log("Chua chon folder.", 'err')
            return
        os.makedirs(output_dir, exist_ok=True)

        exts = ('.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv', '.ts', '.m4v')
        files = sorted(f for f in os.listdir(input_dir) if f.lower().endswith(exts))
        if not files:
            self._log("Khong tim thay video.", 'err')
            return

        run_items = self._begin_batch(files)
        total = len(run_items)
        self._log(f"Tim thay {len(files)} video | {workers} luong | target: {target}s (~{target/3600:.1f}h).", 'info')

        ok_count = [0]
        done_count = [0]

        def update(idx, success):
            with self._lock:
                if success: ok_count[0] += 1
                done_count[0] += 1
                d = done_count[0]
            self._mark_row(idx, success)
            self._js(f"uiApi.setProgress({int(d/total*100)}, '{d}/{total}')")
            self._js(f"uiApi.setStatus('Dang keo dai... {d}/{total}')")

        def pad_one(idx, filename):
            self._js(f"uiApi.updateProcessItem({idx}, 0, 'running')")
            inp = os.path.join(input_dir, filename)
            out = os.path.join(output_dir, filename)
            has_audio = self._has_audio_stream(inp)
            spec = self._probe_video_spec(inp)
            w, h, fps = spec['width'], spec['height'], spec['fps']

            # Use concat filter + lavfi sources because the bundled FFmpeg (2018) lacks `tpad`.
            base = [
                self.ffmpeg_path, '-i', inp,
                '-f', 'lavfi', '-t', str(target), '-i', f'color=c=black:s={w}x{h}:r={fps}',
                '-f', 'lavfi', '-t', str(target), '-i', 'anullsrc=r=48000:cl=stereo',
            ]
            if has_audio:
                fc = (
                    f'[0:v]scale={w}:{h},setsar=1,fps={fps},format=yuv420p[v0];'
                    f'[1:v]format=yuv420p[v1];'
                    f'[v0][v1]concat=n=2:v=1:a=0[vout];'
                    f'[0:a]aformat=sample_rates=48000:channel_layouts=stereo[a0];'
                    f'[2:a]aformat=sample_rates=48000:channel_layouts=stereo[a1];'
                    f'[a0][a1]concat=n=2:v=0:a=1[aout]'
                )
                maps = ['-map', '[vout]', '-map', '[aout]']
            else:
                fc = (
                    f'[0:v]scale={w}:{h},setsar=1,fps={fps},format=yuv420p[v0];'
                    f'[1:v]format=yuv420p[v1];'
                    f'[v0][v1]concat=n=2:v=1:a=0[vout]'
                )
                maps = ['-map', '[vout]', '-map', '2:a']

            parts = base[1:] + [
                '-filter_complex', fc,
            ] + maps + [
                '-t', str(target),
                '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '28',
                '-pix_fmt', 'yuv420p',
                '-c:a', 'aac', '-b:a', '64k',
                out
            ]
            if gpu:
                parts = self._swap_to_gpu(parts)
            cmd = [self.ffmpeg_path] + parts + ['-progress', 'pipe:1', '-nostats', '-y']

            success, _ = self._run_ffmpeg_with_table(cmd, idx, target, filename)
            return success

        futures = {}
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for i, f in run_items:
                if self._stopped: break
                futures[ex.submit(pad_one, i, f)] = i
            for fut in as_completed(futures):
                idx = futures[fut]
                try:
                    success = fut.result()
                except Exception:
                    success = False
                update(idx, success)

        self._log(f"=== Hoan thanh: {ok_count[0]}/{total} video ===", 'ok')
        self._js(f"uiApi.setStatus('Xong! {ok_count[0]}/{total} video.')")

    # ── GPU Detection ──

    _gpu_encoder = None
    _gpu_checked = False
    _has_scale_cuda = False

    def _detect_gpu(self):
        """Detect available GPU encoder. Test encode to confirm it works."""
        if self._gpu_checked:
            return self._gpu_encoder
        self._gpu_checked = True

        try:
            r = subprocess.run(
                [self.ffmpeg_path, '-hide_banner', '-encoders'],
                capture_output=True, text=True,
                creationflags=subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS
            )
            output = r.stdout
        except Exception:
            self._gpu_encoder = None
            return None

        # Test each GPU encoder with a real encode
        candidates = []
        if 'h264_nvenc' in output:
            candidates.append(('nvenc', 'h264_nvenc'))
        if 'h264_amf' in output:
            candidates.append(('amf', 'h264_amf'))
        if 'h264_qsv' in output:
            candidates.append(('qsv', 'h264_qsv'))

        for gpu_name, encoder in candidates:
            if self._test_gpu_encoder(encoder):
                self._gpu_encoder = gpu_name
                labels = {'nvenc': 'NVIDIA NVENC', 'amf': 'AMD AMF', 'qsv': 'Intel QSV'}
                self._log(f"GPU detected: {labels[gpu_name]} (da test OK)", 'ok')
                # Check if scale_cuda filter exists
                if gpu_name == 'nvenc':
                    self._has_scale_cuda = 'scale_cuda' in output or self._test_scale_cuda()
                    if self._has_scale_cuda:
                        self._log("scale_cuda: co", 'info')
                    else:
                        self._log("scale_cuda: khong co, dung scale CPU", 'info')
                return self._gpu_encoder

        self._gpu_encoder = None
        self._log("Khong co GPU encoder kha dung, su dung CPU", 'info')
        return None

    def _test_scale_cuda(self):
        try:
            r = subprocess.run(
                [self.ffmpeg_path, '-filters'],
                capture_output=True, text=True, timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS
            )
            return 'scale_cuda' in r.stdout
        except Exception:
            return False

    def _test_gpu_encoder(self, encoder):
        """Test GPU encoder with a tiny encode to confirm it actually works."""
        try:
            r = subprocess.run(
                [self.ffmpeg_path, '-f', 'lavfi', '-i', 'color=c=black:s=256x256:d=0.1',
                 '-c:v', encoder, '-f', 'null', '-', '-y'],
                capture_output=True, text=True, timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS
            )
            return r.returncode == 0
        except Exception:
            return False

    def _swap_to_gpu(self, cmd_parts):
        """Replace CPU encoder with GPU encoder in command parts. Add HW accel decode."""
        gpu = self._detect_gpu()
        if not gpu:
            return cmd_parts

        gpu_map = {
            'nvenc': {'libx264': 'h264_nvenc', 'libx265': 'hevc_nvenc'},
            'amf':   {'libx264': 'h264_amf',   'libx265': 'hevc_amf'},
            'qsv':   {'libx264': 'h264_qsv',   'libx265': 'hevc_qsv'},
        }
        preset_map = {
            'nvenc': 'fast',
            'amf':   'speed',
            'qsv':   'fast',
        }
        # HW accel decode flags (insert before -i)
        hwaccel_map = {
            'nvenc': ['-hwaccel', 'cuda', '-hwaccel_output_format', 'cuda'],
            'amf':   ['-hwaccel', 'dxva2'],
            'qsv':   ['-hwaccel', 'qsv'],
        }
        replacements = gpu_map.get(gpu, {})

        new_parts = []
        for p in cmd_parts:
            new_parts.append(replacements.get(p, p))

        # Insert hwaccel flags before first -i
        hwaccel_flags = hwaccel_map.get(gpu, [])
        if hwaccel_flags:
            # If no scale_cuda, don't use hwaccel_output_format cuda
            # (need CPU frames for scale filter)
            if gpu == 'nvenc' and not self._has_scale_cuda:
                hwaccel_flags = ['-hwaccel', 'cuda']
            try:
                i_idx = new_parts.index('-i')
                new_parts = new_parts[:i_idx] + hwaccel_flags + new_parts[i_idx:]
            except ValueError:
                pass

        # For NVIDIA CUDA with scale_cuda support
        if gpu == 'nvenc' and self._has_scale_cuda:
            new_parts = [p.replace('scale=', 'scale_cuda=') if p.startswith('scale=') else p for p in new_parts]

        result = []
        skip_next = False
        for idx, p in enumerate(new_parts):
            if skip_next:
                skip_next = False
                continue
            if p == '-crf' and gpu:
                skip_next = True
                crf_val = new_parts[idx + 1] if idx + 1 < len(new_parts) else '23'
                if gpu == 'nvenc':
                    result.extend(['-rc', 'constqp', '-qp', crf_val])
                elif gpu == 'amf':
                    result.extend(['-rc', 'cqp', '-qp_i', crf_val, '-qp_p', crf_val])
                elif gpu == 'qsv':
                    result.extend(['-global_quality', crf_val])
            elif p == '-preset' and gpu:
                result.append('-preset')
                skip_next = True
                result.append(preset_map.get(gpu, 'fast'))
            else:
                result.append(p)
        return result

    # ── FFmpeg runner ──

    def _run_ffmpeg(self, cmd, i, total, duration, label):
        if self._stopped:
            return False, label

        # Limit FFmpeg to half of CPU cores
        cpu_count = os.cpu_count() or 4
        threads = max(1, cpu_count // 2)
        cmd = [cmd[0], '-threads', str(threads)] + cmd[1:]

        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 text=True, creationflags=subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS)

        # Track process for pause/stop
        self._current_procs.append(proc)

        stderr_lines = []

        def drain():
            for line in proc.stderr:
                stderr_lines.append(line)
        t = threading.Thread(target=drain, daemon=True)
        t.start()

        last_pct = -1
        for line in proc.stdout:
            if self._stopped:
                break
            line = line.strip()
            if line.startswith('out_time_ms='):
                try:
                    val = int(line.split('=')[1])
                    if val >= 0 and duration > 0:
                        pct = min(99, int(val / 1_000_000 / duration * 100))
                        if pct >= last_pct + 10:
                            self._log(f"  [{i}/{total}] {label}: {pct}%", 'info')
                            last_pct = pct
                except (ValueError, ZeroDivisionError):
                    pass
        proc.wait()
        t.join()

        # Remove from tracked processes
        if proc in self._current_procs:
            self._current_procs.remove(proc)

        if self._stopped:
            return False, label

        if proc.returncode == 0:
            self._log(f"  [{i}/{total}] OK: {label}", 'ok')
            return True, label

        err = ''.join(stderr_lines).strip()
        last_err = err.splitlines()[-1] if err else 'Unknown error'
        self._log(f"  [{i}/{total}] LOI: {last_err}", 'err')
        return False, label

    def _run_ffmpeg_with_table(self, cmd, idx, duration, label):
        """Like _run_ffmpeg but updates process table row instead of log."""
        if self._stopped:
            self._js(f"uiApi.updateProcessItem({idx}, 0, 'stopped')")
            return False, label

        cpu_count = os.cpu_count() or 4
        threads = max(1, cpu_count // 2)
        cmd = [cmd[0], '-threads', str(threads)] + cmd[1:]

        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 text=True, creationflags=subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS)
        self._current_procs.append(proc)

        stderr_lines = []
        def drain():
            for line in proc.stderr:
                stderr_lines.append(line)
        t = threading.Thread(target=drain, daemon=True)
        t.start()

        last_pct = -1
        for line in proc.stdout:
            if self._stopped:
                break
            line = line.strip()
            if line.startswith('out_time_ms='):
                try:
                    val = int(line.split('=')[1])
                    if val >= 0 and duration > 0:
                        pct = min(99, int(val / 1_000_000 / duration * 100))
                        if pct > last_pct:
                            self._js(f"uiApi.updateProcessItem({idx}, {pct}, 'running')")
                            last_pct = pct
                except (ValueError, ZeroDivisionError):
                    pass
        proc.wait()
        t.join()

        if proc in self._current_procs:
            self._current_procs.remove(proc)

        if self._stopped:
            self._js(f"uiApi.updateProcessItem({idx}, {last_pct}, 'stopped')")
            return False, label

        if proc.returncode == 0:
            return True, label

        err = ''.join(stderr_lines).strip()
        last_err = err.splitlines()[-1] if err else 'Unknown error'
        self._log(f"LOI {label}: {last_err}", 'err')
        return False, label

    def _parse_cmd_template(self, template, input_path, output_path):
        """Parse command template, replacing {input}/{output} without breaking paths with spaces."""
        # Replace placeholders with unique tokens
        token_in = '\x00INPUT\x00'
        token_out = '\x00OUTPUT\x00'
        raw = template.replace('{input}', token_in).replace('{output}', token_out)

        # Split on whitespace (safe since tokens have no spaces)
        parts = raw.split()

        # Remove ffmpeg prefix
        if parts and parts[0].lower() in ('ffmpeg', 'ffmpeg.exe'):
            parts = parts[1:]

        # Remove -y
        parts = [p for p in parts if p != '-y']

        # Replace tokens back with actual paths
        result = []
        for p in parts:
            if token_in in p:
                result.append(p.replace(token_in, input_path))
            elif token_out in p:
                result.append(p.replace(token_out, output_path))
            else:
                result.append(p)
        return result

    # ── Helpers ──

    def _probe_spec(self, file_path):
        """Probe a media file and return a spec dict for mismatch detection.

        Returns a dict with keys:
            v_codec, width, height, fps, pix_fmt,
            a_codec, sample_rate, channels
        Returns None on any failure (caller should treat as mismatch → re-encode).
        """
        try:
            r = subprocess.run(
                [
                    self.ffprobe_path,
                    '-v', 'error',
                    '-show_entries',
                    'stream=codec_type,codec_name,width,height,r_frame_rate,pix_fmt,sample_rate,channels',
                    '-of', 'json',
                    file_path,
                ],
                capture_output=True,
                text=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
                timeout=10,
            )
            data = json.loads(r.stdout)
        except Exception as e:
            self._log(f"  [probe] Loi khi probe {os.path.basename(file_path)}: {e}", 'err')
            return None

        try:
            streams = data.get('streams', [])
            v = next((s for s in streams if s.get('codec_type') == 'video'), None)
            a = next((s for s in streams if s.get('codec_type') == 'audio'), None)
            return {
                'v_codec':     (v or {}).get('codec_name'),
                'width':       (v or {}).get('width'),
                'height':      (v or {}).get('height'),
                'fps':         (v or {}).get('r_frame_rate'),
                'pix_fmt':     (v or {}).get('pix_fmt'),
                'a_codec':     (a or {}).get('codec_name'),
                'sample_rate': (a or {}).get('sample_rate'),
                'channels':    (a or {}).get('channels'),
            }
        except Exception as e:
            self._log(f"  [probe] Loi parse JSON {os.path.basename(file_path)}: {e}", 'err')
            return None

    def _get_duration(self, filepath):
        try:
            r = subprocess.run(
                [self.ffprobe_path, '-v', 'quiet', '-print_format', 'json', '-show_format', filepath],
                capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS)
            return float(json.loads(r.stdout)['format']['duration'])
        except Exception:
            return 0.0

    # ── yt-dlp self-update ──

    def updateYtDlp(self):
        threading.Thread(target=self._do_update_ytdlp, daemon=True).start()

    def _do_update_ytdlp(self):
        url = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"
        new_path = self.ytdlp_path + ".new"
        self._log("Dang tai yt-dlp moi nhat...", 'info')
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "RENUP-Updater"})
            with urllib.request.urlopen(req, timeout=120) as resp:
                total = int(resp.headers.get('Content-Length', 0))
                downloaded = 0
                with open(new_path, 'wb') as f:
                    while True:
                        chunk = resp.read(65536)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total > 0:
                            pct = int(downloaded / total * 100)
                            self._log(f"  Tai yt-dlp: {pct}% ({downloaded // 1024 // 1024}MB / {total // 1024 // 1024}MB)", 'info')
            os.replace(new_path, self.ytdlp_path)
            self._log("Da cap nhat yt-dlp.", 'ok')
        except Exception as e:
            self._log(f"Loi cap nhat yt-dlp: {e}", 'err')
            try:
                if os.path.exists(new_path):
                    os.remove(new_path)
            except Exception:
                pass

    # ── Claim Tiktok ──

    def _parse_claim_table(self, text):
        # Cot: [1] Ten Final | [2] Bai Nhac Claim | [3] Link_Voice goc | [4] Link Bai Nhac
        #      | [5] Ten Bai Nhac Goc (bo qua) | [6] Anh Bia Album (bo qua) | [7] Doi tac (bo qua)
        #      | [8] Ten Folder Nhac (optional, dung de route output vao thu muc con)
        rows = []
        # Fill-forward state cho cot [8] (Ten Folder Nhac): mo phong hanh vi merged-cell
        # cua Google Sheets — khi copy ra text, chi dong dau cua vung merge co chu, cac
        # dong con lai trong vung do ra cot trong. Dong trong ke thua gia tri cua dong
        # gan nhat phia tren co chu, cho toi khi gap dong co chu moi.
        last_folder_name = ''
        for raw_line in text.split('\n'):
            line = raw_line.rstrip('\r').strip()
            if not line:
                continue
            parts = line.split('\t')
            if len(parts) < 3:
                continue
            fn = parts[0].strip()
            mn = parts[1].strip()
            vi = parts[2].strip()
            if not fn or not mn or not vi:
                continue
            if not vi.isdigit():
                self._log(f"Bo qua dong tieu de: {line[:80]}", 'info')
                continue
            mu = parts[3].strip() if len(parts) >= 4 else ''
            # parts[4], parts[5], parts[6] (Ten Bai Nhac Goc / Anh Bia Album / Doi tac) bi bo qua
            folder_name = parts[7].strip() if len(parts) >= 8 else ''
            if folder_name:
                last_folder_name = folder_name
            else:
                folder_name = last_folder_name
            rows.append({
                'final_name': fn,
                'music_name': mn,
                'voice_id': vi,
                'music_url': mu,
                'folder_name': folder_name,
            })
        return rows

    def _safe_filename(self, name):
        import re
        return re.sub(r'[\\/:*?"<>|]', '_', name)

    @staticmethod
    def _with_short_suffix(name):
        stripped = name.rstrip()
        if re.search(r'shorts?\s*$', stripped, re.IGNORECASE):
            return stripped
        return stripped + ' short'


    def _download_voice_tiktok(self, voice_id, voice_dir, device_id, idx=None, lo=0, hi=33):
        """Tai voice TikTok qua yt-dlp. idx co gia tri thi bao tien do vao dong idx.

        Vi sao khong the bam thang vao phan tram tai that: do that tren mot voice
        (tong 10.1 giay) cho thay khau TAI chi chiem 0.06 giay - 0.6% thoi gian.
        Phan con lai la khoi dong yt-dlp (5.8 giay, KHONG in ra dong nao), mo
        trang sound + tai danh sach video (3.1 giay), va ffmpeg tach sang mp3
        (0.7 giay). Bam theo "[download] xx%" se cho ra mot thanh do nam im 9.5
        giay roi vut mot cai - dung bang khong sua.

        Cach dung o day: neo vao cac MOC CO THAT (moi moc la mot dong yt-dlp thuc
        su in ra), con giua hai moc thi cho thanh bo dan theo thoi gian, tiem can
        moc ke tiep va KHONG BAO GIO vuot qua no. Nguoi dung thay chuyen dong
        lien tuc, ma khong bao gio bi bao la da qua mot buoc chua xay ra.
        """
        if self._stopped:
            return False
        voice_path = os.path.join(voice_dir, f"{voice_id}.mp3")
        url = f"https://www.tiktok.com/music/original-sound-{voice_id}"
        cmd = [
            self.ytdlp_path,
            "--encoding", "utf-8",
            "--newline",
            "--extractor-args", f"tiktok:device_id={device_id}",
            "--playlist-end", "1",
            "-x", "--audio-format", "mp3",
            "--ffmpeg-location", self.ffmpeg_path,
            "-o", os.path.join(voice_dir, f"{voice_id}.%(ext)s"),
            "--no-playlist",
            url,
        ]
        stderr_lines = []

        span = max(1, hi - lo)
        # Ranh gioi cac giai doan, chia theo DUNG TY LE THOI GIAN da do (tong
        # 10.1 giay tren mot voice):
        #   khoi dong yt-dlp   5.8s -> 57%   (khong in ra dong nao)
        #   mo trang + ds video 3.1s -> 30%
        #   chon dinh dang      0.2s ->  2%
        #   tai that           0.06s ->  1%
        #   ffmpeg tach mp3     0.7s ->  7%
        # Chia theo thoi gian chu khong theo cam tinh la ly do thanh do chay deu:
        # neu cho khau "tai that" mot dai rong thi no se vut qua trong 0.06 giay
        # roi dung im o cho khac.
        P_START = (0.00, 0.57, 5.8)     # (dau, cuoi, so giay du kien)
        P_LIST = (0.57, 0.87, 3.1)
        P_DEST = (0.87, 0.90, 0.3)
        P_DL = (0.90, 0.93, 0.5)
        P_AUDIO = (0.93, 1.00, 0.8)

        st = {'ph': P_START, 't0': time.time(), 'cur': float(lo),
              'last': -1, 'run': True}
        st_lock = threading.Lock()

        def _pct_locked():
            a, b, dur = st['ph']
            k = min(1.0, (time.time() - st['t0']) / max(dur, 0.01))
            # *0.97: khong bao gio cham dung ranh gioi cuoi giai doan khi giai
            # doan do chua thuc su ket thuc.
            return lo + span * (a + (b - a) * k * 0.97)

        def emit():
            with st_lock:
                v = _pct_locked()
                if v > st['cur']:
                    st['cur'] = v
                p = int(st['cur'])
                if p == st['last']:
                    return
                st['last'] = p
            self._js(f"uiApi.updateProcessItem({idx}, {p}, 'running')")

        def ticker():
            while True:
                time.sleep(0.2)
                with st_lock:
                    if not st['run']:
                        return
                emit()

        tick = None
        if idx is not None:
            tick = threading.Thread(target=ticker, daemon=True)
            tick.start()

        def enter(phase):
            """Sang giai doan moi vi mot moc CO THAT vua den."""
            with st_lock:
                if phase[0] < st['ph'][0]:
                    return          # moc den lech thu tu: bo qua, khong lui
                st['ph'] = phase
                st['t0'] = time.time()
                floor = lo + span * phase[0]
                if st['cur'] < floor:
                    st['cur'] = floor
            emit()

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding='utf-8',
                errors='replace',
                creationflags=subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS,
            )
            self._current_procs.append(proc)
            try:
                def drain():
                    for line in proc.stderr:
                        stderr_lines.append(line)
                t = threading.Thread(target=drain, daemon=True)
                t.start()
                for line in proc.stdout:
                    if idx is None:
                        continue
                    line = line.strip()
                    if RE_TT_EXTRACT.match(line) or RE_TT_LIST.match(line):
                        enter(P_LIST)
                    elif RE_YT_DEST.match(line):
                        enter(P_DEST)
                    elif RE_YT_DL.match(line):
                        enter(P_DL)
                    elif RE_TT_AUDIO.match(line):
                        enter(P_AUDIO)
                proc.wait()
                t.join()
            finally:
                if proc in self._current_procs:
                    self._current_procs.remove(proc)
        except Exception as e:
            self._log(f"Loi spawn yt-dlp: {e}", 'err')
            return False
        finally:
            with st_lock:
                st['run'] = False
            if tick is not None:
                tick.join(timeout=1)

        if self._stopped:
            return False

        if proc.returncode == 0 and os.path.exists(voice_path):
            return True

        last_err = stderr_lines[-1].strip() if stderr_lines else 'Unknown error'
        self._log(f"yt-dlp loi (voice {voice_id}): {last_err}", 'err')
        return False

    @staticmethod
    def _extract_drive_file_id(url):
        """Extract a Google Drive file ID from various URL shapes, or a bare ID."""
        if not url:
            return None
        url = url.strip()
        m = re.search(r"/file/d/([A-Za-z0-9_-]+)", url)
        if m:
            return m.group(1)
        m = re.search(r"[?&]id=([A-Za-z0-9_-]+)", url)
        if m:
            return m.group(1)
        if re.match(r"^[A-Za-z0-9_-]{20,}$", url):
            return url
        return None

    def _download_music_drive(self, url, dest_path, idx=None, lo=33, hi=66):
        """Download a public Google Drive file directly over HTTP (no gdown).

        idx/lo/hi (tuy chon): so thu tu dong trong bang tien trinh va dai phan
        tram danh cho buoc nay. Truyen vao thi ham bao tien do + toc do trong
        luc tai; bo qua thi im lang y het truoc day.

        Google Drive's public-download flow redirects to
        https://drive.usercontent.google.com/download . For small files this
        returns the raw bytes immediately. For large files it first returns an
        HTML "can't scan for viruses" confirmation page containing a form with
        hidden inputs (confirm/uuid/id/export) that must be resubmitted to get
        the actual file bytes.

        Writes to a unique `.part` temp file first, then atomically replaces
        `dest_path` once the download is verified complete. This keeps the
        (cached, reused-across-runs) `dest_path` free of partial/corrupt data
        if the download fails or is stopped mid-way, and is safe even if two
        workers download the same cached filename concurrently (each uses its
        own `.part`, last `os.replace()` wins, `dest_path` is always valid).
        """
        part_path = f"{dest_path}.{uuid.uuid4().hex}.part"
        # Bat tay HTTP voi Drive (co the phai qua trang xac nhan virus) mat ~2.6
        # giay do duoc, va trong khoang do chua co byte nao de tinh phan tram.
        # Danh 3 diem dau dai cho no, bo theo thoi gian; phan truyen that bat dau
        # tu dl_lo nen thanh khong bao gio lui.
        dl_lo = lo + 3 if hi - lo > 6 else lo
        connected = threading.Event()
        if idx is not None:
            def _creep():
                c0 = time.time()
                shown = lo
                while not connected.wait(0.25):
                    k = min(1.0, (time.time() - c0) / 2.6)
                    p = lo + int((dl_lo - lo) * k * 0.97)
                    if p != shown:
                        shown = p
                        self._js(f"uiApi.updateProcessItem({idx}, {p}, 'running')")
            threading.Thread(target=_creep, daemon=True).start()
        try:
            if self._stopped:
                return False

            file_id = self._extract_drive_file_id(url)
            if not file_id:
                self._log(f"Loi tai nhac Drive: khong doc duoc file ID tu link: {url}", 'err')
                return False

            cookie_jar = urllib.request.HTTPCookieProcessor()
            opener = urllib.request.build_opener(cookie_jar)
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) RENUP"}

            base = "https://drive.usercontent.google.com/download"
            download_url = f"{base}?id={file_id}&export=download&confirm=t"

            def _fetch(u):
                req = urllib.request.Request(u, headers=headers)
                return opener.open(req, timeout=60)

            resp = _fetch(download_url)
            content_type = resp.headers.get('Content-Type', '')

            if content_type.startswith('text/html'):
                # Confirmation page for large files: parse hidden form inputs and resubmit.
                html = resp.read().decode('utf-8', errors='ignore')
                params = {}
                for name in ("id", "export", "confirm", "uuid"):
                    m = re.search(
                        rf'<input[^>]+name="{name}"[^>]+value="([^"]*)"', html
                    )
                    if m:
                        params[name] = m.group(1)
                params.setdefault("id", file_id)
                params.setdefault("export", "download")
                params.setdefault("confirm", "t")

                if not params.get("uuid"):
                    self._log("Loi tai nhac Drive: khong parse duoc trang xac nhan (file lon).", 'err')
                    return False

                query = "&".join(f"{k}={v}" for k, v in params.items())
                resp = _fetch(f"{base}?{query}")
                content_type = resp.headers.get('Content-Type', '')
                if content_type.startswith('text/html'):
                    self._log("Loi tai nhac Drive: van nhan HTML sau khi xac nhan (file co the private).", 'err')
                    return False

            # Bao tien do trong luc tai. Khong co doan nay thi dong bang nam im
            # o 33% suot ca lan tai - va file nhac Drive thuc te nang toi vai
            # chuc MB (do that: 47 MB moi dong), chay 1 luong thi nguoi dung
            # nhin thay y het nhu treo. Da bi bao la "loi" dung vi ly do nay.
            try:
                total_bytes = int(resp.headers.get('Content-Length') or 0)
            except (TypeError, ValueError):
                total_bytes = 0
            connected.set()     # het pha bat tay, tu day co byte de dem that
            got = 0
            last_t = time.time()
            last_got = 0
            last_pct = -1
            speed = ''

            with open(part_path, 'wb') as f:
                while True:
                    if self._stopped:
                        return False
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
                    if idx is None:
                        continue
                    got += len(chunk)
                    now = time.time()
                    pct = min(dl_lo + int(got / total_bytes * (hi - dl_lo)), hi) if total_bytes else dl_lo
                    # Day khi phan tram doi (nhieu nhat hi-lo = 33 lan cho ca file)
                    # HOAC moi nua giay de con cap nhat toc do. Khong duoc day theo
                    # tung khoi 64 KB: file 47 MB se thanh ~750 lan goi evaluate_js.
                    due = now - last_t >= 0.5
                    if due:
                        speed = self._fmt_rate_kb((got - last_got) / 1024.0 / (now - last_t))
                        last_t, last_got = now, got
                    if due or pct != last_pct:
                        last_pct = pct
                        payload = json.dumps(speed) if speed else "''"
                        self._js(f"uiApi.updateProcessItem({idx}, {pct}, 'running', {payload})")

            if not os.path.exists(part_path) or os.path.getsize(part_path) == 0:
                return False

            os.replace(part_path, dest_path)
            return True
        except Exception as e:
            self._log(f"Loi tai nhac Drive: {e}", 'err')
            return False
        finally:
            connected.set()     # dung pha bat tay du co loi hay bi Stop
            if os.path.exists(part_path):
                try:
                    os.remove(part_path)
                except Exception:
                    pass

    def _concat_voice_music(self, voice_path, music_path, out_path, sr, ch, idx, max_seconds=60):
        """Concat voice (full) + music (full), hard-capped at max_seconds via -t.

        Previous implementation trimmed music by an estimated
        `max_seconds - voice_dur` (atrim), where voice_dur came from ffprobe.
        For MP3 voice files, ffprobe's reported duration does not always match
        the actual decoded sample count (MP3 frame/encoder-delay slack), so the
        trimmed music length was estimated from a slightly-off voice duration,
        producing outputs ~40ms short (e.g. 59.96s instead of 60.000s).

        Fix: concat voice (full) + music (full) unfiltered, then let `-t
        {max_seconds}` cut the final PCM stream. On pcm_s16le, `-t` is
        sample-accurate, so the output is exactly max_seconds seconds whenever
        voice+music together reach that mark, regardless of voice_dur precision.
        If voice alone already exceeds max_seconds, `-t` cuts within the voice
        portion. If voice+music together are shorter than max_seconds, the
        output is simply shorter (music genuinely ran out) — same behavior as
        before.
        """
        layout = 'stereo' if ch == 2 else 'mono'
        fc = (
            f"[0:a]aresample={sr},aformat=sample_fmts=fltp:channel_layouts={layout},"
            f"asetpts=PTS-STARTPTS[a0];"
            f"[1:a]aresample={sr},aformat=sample_fmts=fltp:channel_layouts={layout},"
            f"asetpts=PTS-STARTPTS[a1];"
            f"[a0][a1]concat=n=2:v=0:a=1[a]"
        )
        cmd = [
            self.ffmpeg_path,
            "-i", voice_path,
            "-i", music_path,
            "-filter_complex", fc,
            "-map", "[a]",
            "-c:a", "pcm_s16le",
            "-t", str(max_seconds),
            "-progress", "pipe:1", "-nostats",
            out_path, "-y",
        ]

        success, _ = self._run_ffmpeg_with_table(cmd, idx, max_seconds, os.path.basename(out_path))
        return success

    def _run_claim_tiktok(self, params, code):
        self._js("uiApi.setStatus('Dang chuan bi Claim Tiktok...')")
        self._js("uiApi.setProgress(0, '')")
        self._log("=== Bat dau Claim Tiktok ===", 'info')

        claim_table = params.get('claimTable', '').strip()
        voice_dir = params.get('voiceDir', '').strip()
        music_dir = params.get('musicDir', '').strip()
        output_dir = params.get('outputDir', '').strip()
        workers = max(1, params.get('workers', 2))

        device_id = code.get('device_id', '7300000000000000000')
        sample_rate = int(code.get('sample_rate', 44100))
        channels = int(code.get('channels', 2))
        max_seconds = int(code.get('max_seconds', 60))

        if not claim_table:
            self._log("Bang rong.", 'err')
            return
        if not voice_dir:
            self._log("Chua chon folder Voice.", 'err')
            return
        if not music_dir:
            self._log("Chua chon folder Music.", 'err')
            return
        if not output_dir:
            self._log("Chua chon folder Output.", 'err')
            return
        if not os.path.exists(self.ffmpeg_path):
            self._log("Khong tim thay ffmpeg.exe", 'err')
            return
        if not os.path.exists(self.ytdlp_path):
            self._log("Khong tim thay yt-dlp.exe (vao Cap nhat yt-dlp).", 'err')
            return

        os.makedirs(voice_dir, exist_ok=True)
        os.makedirs(music_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)

        rows = self._parse_claim_table(claim_table)
        if not rows:
            self._log("Khong co dong hop le trong bang.", 'err')
            return

        labels = [r['final_name'] for r in rows]
        run_items = self._begin_batch(labels)
        total = len(run_items)

        ok_count = [0]
        done_count = [0]

        def update(idx, success):
            with self._lock:
                if success:
                    ok_count[0] += 1
                done_count[0] += 1
                d = done_count[0]
            self._mark_row(idx, success)
            self._js(f"uiApi.setProgress({int(d / total * 100)}, '{d}/{total}')")
            self._js(f"uiApi.setStatus('Dang xu ly... {d}/{total} dong')")

        def process_one(idx, row):
            self._js(f"uiApi.updateProcessItem({idx}, 0, 'running')")
            final_name = row['final_name']
            music_name = row['music_name']
            voice_id = row['voice_id']
            music_url = row['music_url']
            folder_name = row.get('folder_name', '').strip()
            try:
                if self._stopped:
                    return False

                voice_path = os.path.join(voice_dir, f"{voice_id}.mp3")
                if not os.path.exists(voice_path):
                    ok = self._download_voice_tiktok(voice_id, voice_dir, device_id, idx=idx)
                    if not ok or not os.path.exists(voice_path):
                        self._log(f"[{idx + 1}] Voice fail: {voice_id}", 'err')
                        return False
                self._js(f"uiApi.updateProcessItem({idx}, 33, 'running')")

                music_path = os.path.join(music_dir, self._safe_filename(music_name) + '.wav')
                if not os.path.exists(music_path):
                    if not music_url:
                        self._log(f"[{idx + 1}] Thieu link nhac dong: {final_name}", 'err')
                        return False
                    if self._stopped:
                        return False
                    ok = self._download_music_drive(music_url, music_path, idx=idx)
                    if not ok or not os.path.exists(music_path):
                        self._log(f"[{idx + 1}] Tai nhac fail: {final_name}", 'err')
                        return False
                self._js(f"uiApi.updateProcessItem({idx}, 66, 'running')")

                if folder_name:
                    dir_name = self._with_short_suffix(folder_name)
                    row_output_dir = os.path.join(output_dir, self._safe_filename(dir_name))
                    os.makedirs(row_output_dir, exist_ok=True)
                else:
                    row_output_dir = output_dir
                out_path = os.path.join(row_output_dir, self._safe_filename(final_name) + '.wav')
                ok = self._concat_voice_music(voice_path, music_path, out_path, sample_rate, channels, idx, max_seconds)
                return ok

            except Exception as e:
                self._log(f"[{idx + 1}] LOI: {e}", 'err')
                return False

        self._log(f"Tim thay {len(rows)} dong | {workers} luong.", 'info')
        # Bat buoc: khong co dong nay thi thanh trang thai ket o "Dang chuan bi
        # Claim Tiktok..." cho toi khi dong DAU TIEN xong (setStatus chi duoc goi
        # trong update(), tuc sau moi dong). Dong dau co the mat vai phut vi phai
        # tai file nhac vai chuc MB -> nguoi dung tuong app treo.
        self._js(f"uiApi.setStatus('Dang xu ly... 0/{total} dong')")

        futures = {}
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for i, _lb in run_items:
                if self._stopped:
                    break
                futures[ex.submit(process_one, i, rows[i])] = i
            for fut in as_completed(futures):
                idx = futures[fut]
                try:
                    success = fut.result()
                except Exception as e:
                    self._log(f"LOI: {e}", 'err')
                    success = False
                update(idx, success)

        self._log(f"=== Hoan thanh: {ok_count[0]}/{total} dong ===", 'ok')
        self._js(f"uiApi.setStatus('Xong! {ok_count[0]}/{total} dong.')")

    # ── Claim Jazz Youtube (ADR-019) ──

    @staticmethod
    def _jazz_parse_noi_table(text):
        """Doc bang nhac noi. Moi dong: 'Ten bai <TAB> link Drive', hoac chi link.

        Tra ve [{'name', 'url', 'file_id'}, ...], da bo dong trung file_id.
        Ten bai chi de doc log va lam ten file cache; DANH TINH dung cho quota
        la file_id cua Drive - doi ten bai khong lam mat lich su da dung.
        """
        out, seen = [], set()
        for raw in (text or '').splitlines():
            line = raw.strip()
            if not line:
                continue
            parts = [p.strip() for p in line.split('\t')]
            if len(parts) >= 2 and parts[1]:
                name, url = parts[0], parts[1]
            else:
                name, url = '', parts[0]
            fid = Api._extract_drive_file_id(url)
            if not fid:
                continue
            if fid in seen:
                continue
            seen.add(fid)
            out.append({'name': name or fid, 'url': url, 'file_id': fid})
        return out

    @staticmethod
    def _jazz_pick_slots(region_start, region_end, lengths, rng):
        """Chon vi tri dat cac doan noi, ngau nhien nhung KHONG chong lan.

        Chia [region_start, region_end] thanh len(lengths) o bang nhau roi boc
        ngau nhien trong tung o. Chia o truoc khi boc la cach re nhat de bao
        dam khong chong lan ma van ngau nhien - boc tu do roi kiem tra va boc
        lai co the lap vo han khi vung qua chat.

        Tra ve list moc bat dau, tang dan. None neu khong du cho.
        """
        n = len(lengths)
        if n == 0:
            return []
        span = region_end - region_start
        if span <= 0:
            return None
        slot = span / n
        if slot < max(lengths):
            return None            # o hep hon doan can dat -> khong du cho
        starts = []
        for i, L in enumerate(lengths):
            lo = region_start + i * slot
            hi = lo + slot - L
            starts.append(round(rng.uniform(lo, max(lo, hi)), 3))
        return starts

    def _jazz_load_state(self, path):
        """Doc so dung nhac noi. Cau truc: {'used': {file_id: [chi so phan]}}."""
        try:
            with open(path, 'r', encoding='utf-8') as fh:
                data = json.load(fh)
            used = data.get('used', {})
            return {k: sorted(set(int(i) for i in v))
                    for k, v in used.items() if isinstance(v, list)}
        except Exception:
            return {}

    def _jazz_save_state(self, path, used):
        """Ghi so dung. Ghi ra .part roi os.replace de khong bao gio de lai
        file do dang neu app bi tat giua chung (cung cach _download_music_drive
        dang lam)."""
        tmp = path + '.part'
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(tmp, 'w', encoding='utf-8') as fh:
                json.dump({'used': {k: sorted(v) for k, v in used.items()}},
                          fh, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        except Exception as e:
            self._log(f"Khong ghi duoc so dung nhac noi: {e}", 'err')

    def _jazz_build_filter(self, dur_goc, slots, seg_specs, has_cid):
        """Dung chuoi filter_complex thay the cac doan trong nhac goc.

        Quy uoc input: 0 = danh sach video (concat demuxer), 1 = nhac goc,
        2..n = cac file nhac noi theo dung thu tu seg_specs, cuoi cung = CID.

        seg_specs: [(input_index, start_trong_bai_noi, do_dai), ...] doi ung
        1-1 voi slots (da sap tang dan).
        """
        parts, labels, cur = [], [], 0.0
        for i, (start, (inp, s_off, L)) in enumerate(zip(slots, seg_specs)):
            if start > cur + 0.001:
                parts.append(f"[1:a]atrim={cur:.3f}:{start:.3f},"
                             f"asetpts=PTS-STARTPTS[g{i}]")
                labels.append(f"[g{i}]")
            parts.append(f"[{inp}:a]atrim={s_off:.3f}:{s_off + L:.3f},"
                         f"asetpts=PTS-STARTPTS[n{i}]")
            labels.append(f"[n{i}]")
            cur = start + L          # THAY THE: bo doan goc dai bang doan noi
        if dur_goc > cur + 0.001:
            parts.append(f"[1:a]atrim={cur:.3f}:{dur_goc:.3f},"
                         f"asetpts=PTS-STARTPTS[gz]")
            labels.append("[gz]")
        if has_cid:
            cid_idx = 2 + len(seg_specs)
            parts.append(f"[{cid_idx}:a]asetpts=PTS-STARTPTS[cid]")
            labels.append("[cid]")
        parts.append(f"{''.join(labels)}concat=n={len(labels)}:v=0:a=1[aout]")
        return ';'.join(parts)

    def _run_claim_jazz(self, params, code):
        """Loop hinh cho du thoi luong nhac goc, thay the vai doan sau moc
        1h30 bang nhac noi tai tu Drive, roi gan nhac CID vao cuoi. ADR-019.
        """
        video_dir = (params.get('inputDir') or '').strip()
        folders = params.get('folders') or {}
        goc_dir = (folders.get('jazzGoc') or '').strip()
        cid_dir = (folders.get('jazzCid') or '').strip()
        noi_dir = (params.get('jazzNoiDir') or '').strip()
        noi_table = params.get('jazzNoiTable') or ''
        output_dir = (params.get('outputDir') or '').strip()
        workers = max(1, int(params.get('workers') or 1))

        vid_ext = [e.lower() for e in code.get(
            'video_ext', ['.mp4', '.mkv', '.mov', '.avi', '.ts', '.m4v'])]
        aud_ext = [e.lower() for e in code.get(
            'audio_ext', ['.mp3', '.wav', '.m4a', '.aac', '.flac', '.ogg'])]
        n_parts = max(1, int(code.get('noi_parts', 5)))
        insert_after = float(code.get('insert_after_seconds', 5400))
        sr = int(code.get('sample_rate', 44100))
        ch = int(code.get('channels', 2))
        abr = str(code.get('audio_bitrate', '192k'))
        state_name = str(code.get('state_file', 'claim_jazz_state.json'))

        try:
            n_noi = int(str(params.get('jazzNoiCount') or '').strip()
                        or code.get('default_noi_count', 4))
        except ValueError:
            n_noi = int(code.get('default_noi_count', 4))

        self._js("uiApi.setStatus('Dang chuan bi Claim Jazz...')")
        self._js("uiApi.setProgress(0, '')")
        self._log("=== Bat dau Claim Jazz Youtube ===", 'info')

        for label, path in (("Kho Video", video_dir), ("Kho Nhac goc", goc_dir),
                            ("Kho CID", cid_dir),
                            ("Folder cache nhac noi", noi_dir),
                            ("Output", output_dir)):
            if not path:
                self._log(f"Chua chon {label}.", 'err')
                return
        for label, path in (("Kho Video", video_dir), ("Kho Nhac goc", goc_dir),
                            ("Kho CID", cid_dir)):
            if not os.path.isdir(path):
                self._log(f"Khong thay {label}: {path}", 'err')
                return
        if not os.path.exists(self.ffmpeg_path):
            self._log("Khong tim thay ffmpeg.exe", 'err')
            return
        if n_noi < 1:
            self._log("So bai nhac noi phai tu 1 tro len.", 'err')
            return

        videos = sorted(f for f in os.listdir(video_dir)
                        if os.path.splitext(f)[1].lower() in vid_ext)
        gocs = sorted(f for f in os.listdir(goc_dir)
                      if os.path.splitext(f)[1].lower() in aud_ext)
        cids = sorted(f for f in os.listdir(cid_dir)
                      if os.path.splitext(f)[1].lower() in aud_ext)
        for label, lst in (("Kho Video", videos), ("Kho Nhac goc", gocs),
                           ("Kho CID", cids)):
            if not lst:
                self._log(f"{label} rong.", 'err')
                return

        noi_rows = self._jazz_parse_noi_table(noi_table)
        if not noi_rows:
            self._log("Bang nhac noi rong hoac khong co link Drive hop le.",
                      'err')
            return

        os.makedirs(output_dir, exist_ok=True)
        os.makedirs(noi_dir, exist_ok=True)

        # --- Tai truoc toan bo nhac noi (cache theo file_id) ---
        self._js("uiApi.setStatus('Dang tai nhac noi tu Drive...')")
        ready = []
        for i, row in enumerate(noi_rows):
            if self._stopped:
                return
            dest = os.path.join(noi_dir, f"{row['file_id']}.mp3")
            if not os.path.exists(dest):
                self._log(f"Tai nhac noi {i + 1}/{len(noi_rows)}:"
                          f" {row['name']}", 'info')
                if not self._download_music_drive(row['url'], dest):
                    self._log(f"  Tai fail, bo qua bai: {row['name']}", 'err')
                    continue
            d = self._get_duration(dest)
            if d <= 0:
                self._log(f"  Khong doc duoc thoi luong, bo qua:"
                          f" {row['name']}", 'err')
                continue
            row['path'], row['dur'] = dest, d
            ready.append(row)
        if not ready:
            self._log("Khong co bai nhac noi nao dung duoc.", 'err')
            return
        self._log(f"Nhac noi san sang: {len(ready)} bai"
                  f" (moi bai cat {n_parts} phan).", 'ok')

        state_path = os.path.join(os.path.dirname(self.ffmpeg_path), state_name)
        used = self._jazz_load_state(state_path)

        run_items = self._begin_batch(gocs)
        total = len(run_items)
        self._log(f"Tim thay {len(gocs)} bai nhac goc | {len(videos)} video"
                  f" | {workers} luong | ghep {n_noi} bai noi.", 'info')

        ok_count, done_count = [0], [0]
        rng = random.Random()

        def reserve(k):
            """Giu cho k bai noi con phan chua dung. Tra ve
            [(row, chi_so_phan), ...] hoac None. Phai goi trong khoa: hai dong
            chay song song ma cung boc mot phan thi quota sai va hai video
            chua cung mot doan nhac."""
            with self._lock:
                avail = [r for r in ready
                         if len(used.get(r['file_id'], [])) < n_parts]
                if len(avail) < k:
                    return None
                picked = rng.sample(avail, k)
                out = []
                for r in picked:
                    done = set(used.get(r['file_id'], []))
                    free = [i for i in range(n_parts) if i not in done]
                    idx_part = rng.choice(free)
                    used.setdefault(r['file_id'], []).append(idx_part)
                    out.append((r, idx_part))
                self._jazz_save_state(state_path, used)
                return out

        def release(picked):
            with self._lock:
                for r, idx_part in picked:
                    lst = used.get(r['file_id'], [])
                    if idx_part in lst:
                        lst.remove(idx_part)
                self._jazz_save_state(state_path, used)

        def update(idx, success):
            with self._lock:
                if success:
                    ok_count[0] += 1
                done_count[0] += 1
                d = done_count[0]
            self._mark_row(idx, success)
            self._js(f"uiApi.setProgress({int(d / total * 100)}, '{d}/{total}')")
            self._js(f"uiApi.setStatus('Dang xu ly... {d}/{total} video')")

        def make_one(idx, goc_name):
            self._js(f"uiApi.updateProcessItem({idx}, 0, 'running')")
            goc_path = os.path.join(goc_dir, goc_name)
            dur_goc = self._get_duration(goc_path)
            if dur_goc <= 0:
                self._log(f"[{idx + 1}] Khong doc duoc thoi luong nhac goc:"
                          f" {goc_name}", 'err')
                return False
            if dur_goc <= insert_after:
                self._log(f"[{idx + 1}] Nhac goc chi dai {dur_goc / 60:.1f}"
                          f" phut, ngan hon moc {insert_after / 60:.0f} phut"
                          f" nen khong co cho ghep: {goc_name}", 'err')
                return False

            picked = reserve(n_noi)
            if picked is None:
                left = sum(max(0, n_parts - len(used.get(r['file_id'], [])))
                           for r in ready)
                self._log(f"[{idx + 1}] Khong du bai nhac noi con luot dung"
                          f" (can {n_noi} bai, con {left} phan tren"
                          f" {len(ready)} bai): {goc_name}", 'err')
                return False

            tmp_list = None
            try:
                lengths = [r['dur'] / n_parts for r, _ in picked]
                slots = self._jazz_pick_slots(insert_after, dur_goc, lengths,
                                              rng)
                if slots is None:
                    self._log(f"[{idx + 1}] Khong du cho de dat {n_noi} doan"
                              f" sau moc {insert_after / 60:.0f} phut:"
                              f" {goc_name}", 'err')
                    release(picked)
                    return False

                cid_name = rng.choice(cids)
                cid_path = os.path.join(cid_dir, cid_name)
                dur_cid = max(0.0, self._get_duration(cid_path))
                vid_name = rng.choice(videos)
                vid_path = os.path.join(video_dir, vid_name)
                dur_vid = self._get_duration(vid_path)
                if dur_vid <= 0:
                    self._log(f"[{idx + 1}] Khong doc duoc thoi luong video:"
                              f" {vid_name}", 'err')
                    release(picked)
                    return False

                total_dur = dur_goc + dur_cid
                n_rep = int(total_dur // dur_vid) + 1

                self._log(f"[{idx + 1}/{total}] {goc_name}"
                          f" | hinh: {vid_name} x{n_rep}"
                          f" | CID: {cid_name}", 'info')
                for (r, ip), st in zip(picked, slots):
                    self._log(f"      noi: {r['name']} phan {ip + 1}/{n_parts}"
                              f" -> dat o {self._fmt_seconds(int(st))}", 'info')

                tmp_list = os.path.join(
                    output_dir, f"_jazz_{uuid.uuid4().hex}.txt")
                with open(tmp_list, 'w', encoding='utf-8') as fh:
                    for _ in range(n_rep):
                        fh.write("file '" + vid_path.replace('\\', '/')
                                 + "'\n")

                seg_specs = [(2 + i, ip * (r['dur'] / n_parts),
                              r['dur'] / n_parts)
                             for i, (r, ip) in enumerate(picked)]
                fc = self._jazz_build_filter(dur_goc, slots, seg_specs,
                                             dur_cid > 0)

                cmd = [self.ffmpeg_path,
                       '-f', 'concat', '-safe', '0', '-i', tmp_list,
                       '-i', goc_path]
                for r, _ip in picked:
                    cmd += ['-i', r['path']]
                if dur_cid > 0:
                    cmd += ['-i', cid_path]
                cmd += ['-filter_complex', fc,
                        '-map', '0:v:0', '-map', '[aout]',
                        '-c:v', 'copy', '-c:a', 'aac', '-b:a', abr,
                        '-ar', str(sr), '-ac', str(ch),
                        '-t', f'{total_dur:.3f}',
                        '-movflags', '+faststart',
                        os.path.join(output_dir,
                                     self._safe_filename(
                                         os.path.splitext(goc_name)[0])
                                     + '.mp4'),
                        '-progress', 'pipe:1', '-nostats', '-y']
                success, _ = self._run_ffmpeg_with_table(
                    cmd, idx, total_dur, goc_name)
                if not success:
                    release(picked)
                return success
            except Exception as e:
                self._log(f"[{idx + 1}] LOI: {e}", 'err')
                release(picked)
                return False
            finally:
                if tmp_list and os.path.exists(tmp_list):
                    try:
                        os.remove(tmp_list)
                    except OSError:
                        pass

        futures = {}
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for i, name in run_items:
                if self._stopped:
                    break
                futures[ex.submit(make_one, i, name)] = i
            for fut in as_completed(futures):
                i = futures[fut]
                try:
                    success = fut.result()
                except Exception as e:
                    self._log(f"[{i + 1}] LOI: {e}", 'err')
                    success = False
                update(i, success)

        left = sum(max(0, n_parts - len(used.get(r['file_id'], [])))
                   for r in ready)
        self._log(f"=== Hoan thanh: {ok_count[0]}/{total} video"
                  f" | nhac noi con {left} phan chua dung ===", 'ok')
        self._js(f"uiApi.setStatus('Xong! {ok_count[0]}/{total} video.')")

    # ── Youtube Download ──

    @staticmethod
    def _yt_extract_video_id(line):
        """Extract an 11-char Youtube video ID from a URL/line, or a bare ID. None if no match."""
        line = line.strip()
        if not line:
            return None
        for pat in YT_ID_PATTERNS:
            m = pat.search(line)
            if m:
                return m.group(1)
        if YT_ID_BARE.match(line):
            return line
        return None

    @staticmethod
    def _yt_build_format(quality, fmt):
        """Build the yt-dlp -f format selector string for a given quality + format.

        fmt: 'MP4', 'MP3' hoac 'MP4_NOAUDIO' (khong phan biet hoa thuong).
        quality bi bo qua voi MP3.

        'MP4_NOAUDIO' = chi luong hinh, khong am thanh. Khac MP4 o dung mot cho:
        bo phan '+ba' nen yt-dlp chi tai MOT luong thay vi hai roi ghep. Nhanh
        cuoi cung phai la 'bv*[...]' chu KHONG duoc la 'b[...]' - 'b' nghia la
        luong lien mach da co san am thanh, tra ve no la giao nguoc thu nguoi
        dung xin.

        AV1 video is excluded on EVERY branch (both 'Best' and the per-height
        ladders): the bundled ffmpeg (2018, N-91314) cannot mux av01 into MP4
        (verified experimentally: exit 1 on postprocessing/copy for an AV1+AAC
        source, while a VP9+AAC source muxes fine). 'Best' deliberately does
        NOT prefer avc1 first (H.264 on Youtube tops out at 1080p) so it still
        picks the highest resolution available among non-AV1 codecs (usually
        VP9 at 1440p/2160p).

        DUNG them nhanh uu tien avc1 vao 'Best' de "sua" viec no tra ve VP9.
        Chuoi -f chi LOC ra tap format hop le; no khong dien dat duoc "trong
        so cac format con lai thi lay cai nao", va moi nhanh avc1 khong kem
        rang buoc chieu cao deu keo video 4K tut xuong 1080p. Viec chon codec
        khi CUNG do phan giai thuoc ve --format-sort, dat o _yt_download_one
        (preset 'format_sort'). Do that 2026-08-25, video fITHDrND8tE co ca
        avc1 lan vp9 o 360p: khong -S thi yt-dlp lay vp9, co -S thi lay avc1,
        va do phan giai khong doi trong ca hai truong hop.

        Bat bien (KHONG co test tu dong - du an chua co ha tang test, phai giu
        bang mat khi sua): moi selector co gioi han chat luong phai ket thuc
        bang mot nhanh co rang buoc chieu cao (`b[height<={h}][...]`), TUYET
        DOI khong co nhanh `b` tran - video khong co luong nao <= chieu cao
        yeu cau thi phai bao loi ro rang chu khong duoc am tham tai ve ban do
        phan giai cao hon.
        """
        fmt = (fmt or '').strip().upper()
        if fmt == 'MP3':
            return 'ba/b'
        quality = (quality or '').strip()
        if fmt == 'MP4_NOAUDIO':
            if quality == 'Best':
                return 'bv*[vcodec!*=av01]'
            h = quality.rstrip('p')
            return (
                f'bv*[height<={h}][vcodec^=avc1]/'
                f'bv*[height<={h}][vcodec^=vp9]/'
                f'bv*[height<={h}][vcodec!*=av01]'
            )
        if quality == 'Best':
            return (
                'bv*[vcodec!*=av01]+ba[ext=m4a]/'
                'bv*[vcodec!*=av01]+ba/'
                'b[vcodec!*=av01]'
            )
        h = quality.rstrip('p')
        return (
            f'bv*[height<={h}][vcodec^=avc1]+ba[ext=m4a]/'
            f'bv*[height<={h}][vcodec^=vp9]+ba[ext=m4a]/'
            f'bv*[height<={h}][vcodec!*=av01]+ba[ext=m4a]/'
            f'bv*[height<={h}][vcodec!*=av01]+ba/'
            f'b[height<={h}][vcodec!*=av01]'
        )

    def _yt_fetch_titles_api(self, video_ids, api_key, socket_timeout=30):
        """Lay tieu de hang loat qua Youtube Data API v3. Tra dict {id: title}.

        Vi sao co ham nay (ADR-015): pha lay tieu de bang yt-dlp ban MOT lan goi
        cho MOI link. Batch 32 link = 32 lan cao du lieu lien tiep -> Youtube
        chan bot ("Sign in to confirm you're not a bot"), da gap that. Doi client
        chi giam xac suat chu khong bo duoc nguyen nhan.
        API chinh thuc nhan 50 ID trong MOT lan goi, ton 1 don vi han muc (mac
        dinh 10.000/ngay = ~500.000 tieu de/ngay) va khong co chong bot. 32 link
        tu 32 lan goi xuong con 1.

        Tra ve dict CHI CHUA nhung id API tra loi. Id thieu (video rieng tu, da
        xoa, hoac khoa bi tu choi) khong co trong dict -> ben goi tu quay ve
        yt-dlp cho rieng nhung id do. Ham nay KHONG BAO GIO nem loi ra ngoai va
        khong bao gio ghi khoa ra log.
        """
        titles = {}
        if not api_key or not video_ids:
            return titles
        ids = list(dict.fromkeys(video_ids))     # bo trung, giu thu tu
        for i in range(0, len(ids), 50):         # API gioi han 50 id moi lan
            if self._stopped:
                break
            chunk = ids[i:i + 50]
            params = urllib.parse.urlencode({
                'part': 'snippet',
                'id': ','.join(chunk),
                'key': api_key,
                'fields': 'items(id,snippet/title)',
            })
            url = f"https://www.googleapis.com/youtube/v3/videos?{params}"
            try:
                req = urllib.request.Request(url, headers={'Accept': 'application/json'})
                with urllib.request.urlopen(req, timeout=socket_timeout) as resp:
                    data = json.loads(resp.read().decode('utf-8', errors='replace'))
                for it in data.get('items', []):
                    vid = it.get('id')
                    title = (it.get('snippet') or {}).get('title')
                    if vid and title:
                        titles[vid] = title
            except urllib.error.HTTPError as e:
                # Doc than loi de bao DUNG nguyen nhan: khoa sai va het han muc
                # la hai chuyen khac han nhau, va nguoi dung xu ly khac han nhau.
                reason = ''
                try:
                    body = json.loads(e.read().decode('utf-8', errors='replace'))
                    errs = (body.get('error') or {}).get('errors') or []
                    reason = errs[0].get('reason', '') if errs else ''
                except Exception:
                    pass
                if reason in ('quotaExceeded', 'dailyLimitExceeded'):
                    self._log("API Youtube het han muc hom nay. Quay ve lay tieu de bang yt-dlp.", 'err')
                elif reason in ('keyInvalid', 'badRequest', 'forbidden', 'accessNotConfigured'):
                    self._log(f"Khoa API bi tu choi ({reason or e.code}). Kiem tra lai khoa"
                              " va xem da bat 'YouTube Data API v3' chua.", 'err')
                else:
                    self._log(f"API Youtube loi {e.code} ({reason or 'khong ro'}).", 'err')
                break            # loi khoa/han muc thi cac lo sau cung se loi
            except Exception as e:
                self._log(f"Khong goi duoc API Youtube: {e}", 'err')
                break
        return titles

    @staticmethod
    def _parse_time_spec(text):
        """'2:00' -> 120.0 | '1:30:00' -> 5400.0 | '90' -> 90.0 | rong/sai -> None.

        Nhan ba dang de nguoi dung khong phai nho quy uoc: hh:mm:ss, mm:ss, va
        so tran (= GIAY). So tran la cho de hieu nham nhat - '2' la 2 giay chu
        khong phai 2 phut - nen ben goi PHAI in ra cach no da hieu (xem
        _fmt_seconds) ngay dong log dau tien, truoc khi tai bat cu byte nao.
        """
        text = str(text or '').strip()
        if not text:
            return None
        parts = text.split(':')
        if len(parts) > 3:
            return None
        try:
            nums = [float(p.strip()) for p in parts]
        except ValueError:
            return None
        if any(n < 0 for n in nums):
            return None
        # mm/ss chi hop le trong 0..59 khi CO phan dung truoc no
        if len(nums) > 1 and any(n >= 60 for n in nums[1:]):
            return None
        total = 0.0
        for n in nums:
            total = total * 60 + n
        return total

    @staticmethod
    def _fmt_seconds(sec):
        """120 -> '02:00' | 5400 -> '01:30:00'. Dung cho log va cho ten file."""
        sec = int(round(float(sec)))
        h, rem = divmod(sec, 3600)
        m, s = divmod(rem, 60)
        if h:
            return f"{h:02d}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"

    @staticmethod
    def _fmt_rate_kb(kb_per_sec):
        """Doi toc do (kB/giay) thanh chuoi ngan gon: 850.0 -> '850KiB/s'.

        Chi dung cho che do "chi tai mot doan": o do ffmpeg lam viec tai nen
        khong co san chuoi toc do nhu yt-dlp, phai tu tinh tu do chenh cua
        "size=" giua hai dong tien do. Dinh dang bat chuoc yt-dlp de hai che do
        hien giong nhau tren giao dien.
        """
        try:
            kb = float(kb_per_sec)
        except (TypeError, ValueError):
            return ''
        if kb < 0:
            return ''
        if kb >= 1024:
            return f"{kb / 1024:.2f}MiB/s"
        return f"{kb:.0f}KiB/s"

    def _resolve_js_runtime(self, spec):
        """Bien gia tri preset `js_runtimes` thanh doi so cho `--js-runtimes`.

        Tra ve None neu khong dung runtime nao (preset de trong) - khi do KHONG
        truyen co, dung hanh vi truoc khi co truong nay.

        Quy tac:
          ''                -> None
          'quickjs'         -> 'quickjs:<bin/qjs.exe>' neu file do CO,
                               nguoc lai 'quickjs' (de yt-dlp tu tim tren PATH)
          'node' / 'deno'   -> giu nguyen, yt-dlp tu tim tren PATH
          '<ten>:<duong dan>' -> giu nguyen, nguoi dung da chi dinh ro

        Vi sao phai ghep duong dan tuyet doi: yt-dlp chi tim runtime tren PATH,
        va bin/ cua app KHONG nam tren PATH. Da kiem chung: duong dan dung ->
        '[jsc:quickjs] Solving JS challenges using quickjs' + tai duoc that;
        duong dan sai -> 'n challenge solving failed'. Tuc la co nay that su
        doc duong dan chu khong chi doc ten.
        """
        spec = str(spec or '').strip()
        if not spec:
            return None
        if ':' in spec:
            return spec
        if spec.lower() == 'quickjs' and os.path.exists(self.qjs_path):
            return f'quickjs:{self.qjs_path}'
        return spec

    def _yt_fetch_title(self, video_id, socket_timeout, player_client=None,
                        js_runtimes=None):
        """Fetch a video's title via `yt-dlp --skip-download --print`. Returns title string or None.

        player_client: value for `--extractor-args youtube:player_client=<value>`
        (e.g. 'android_vr'). Verified experimentally that yt-dlp's default player
        client rotation (web/web_safari/mweb/tv_simply/android/ios) triggers
        Youtube's "Sign in to confirm you're not a bot" error on every video,
        while 'android_vr' does not require a PO Token and succeeds without any
        JS runtime. Falsy/empty value omits the flag entirely (lets the caller
        disable it via the preset JSON).

        js_runtimes: value for `--js-runtimes <value>` (e.g. 'node'). Empty by
        default -> flag omitted -> behaviour identical to before this field
        existed. Only needed by the FALLBACK client 'web_embedded', which since
        ~2026-08 must solve Youtube's "n challenge" and therefore needs a JS
        runtime; without one it returns ZERO video formats and yt-dlp reports
        only "Only images are available" (ADR-010). 'android_vr' still needs
        nothing. Note that yt-dlp does NOT auto-detect Node even when it is on
        PATH - Node must be enabled explicitly via this flag.
        """
        if self._stopped:
            return None
        url = f"https://www.youtube.com/watch?v={video_id}"
        cmd = [
            self.ytdlp_path,
            "--no-playlist",
            "--skip-download",
            "--no-warnings",
            # BAT BUOC: yt-dlp mac dinh ghi stdout bang preferredencoding() cua Windows
            # (may VN thuong la CP1252) voi error handler 'ignore'. Hau qua do duoc:
            #   'We’re ... Hunt!☃️ Bear Hunt' -> bytes 'We\x92re ... Hunt! Bear Hunt'
            #   - dau nhay cong U+2019 -> 1 byte 0x92 (CP1252) -> doc lai bang UTF-8 la
            #     byte le khong hop le -> errors='replace' bien thanh U+FFFD ('?')
            #   - emoji U+2603 U+FE0F khong co trong CP1252 -> bi XOA THANG
            # Ten file tren dia da xac nhan chua dung U+FFFD, dung nhu chuoi tren.
            # PYTHONIOENCODING KHONG sua duoc (da thu: byte y het) vi yt-dlp tu ma hoa
            # bang preferredencoding(); chi co co --encoding cua chinh no moi doi duoc.
            "--encoding", "utf-8",
            "--socket-timeout", str(socket_timeout),
            "--retries", "2",
            "--print", "%(id)s",
            "--print", "%(title)s",
        ]
        if player_client:
            cmd += ["--extractor-args", f"youtube:player_client={player_client}"]
        if js_runtimes:
            cmd += ["--js-runtimes", js_runtimes]
        cmd.append(url)
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding='utf-8',
                errors='replace',
                creationflags=subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS,
            )
            self._current_procs.append(proc)
            try:
                stdout_data, stderr_data = proc.communicate()
            finally:
                if proc in self._current_procs:
                    self._current_procs.remove(proc)
        except Exception:
            return None

        if proc.returncode != 0:
            # Surface the real yt-dlp reason (e.g. geo-block, video removed,
            # sign-in required) instead of swallowing it — the caller only
            # gets None and would otherwise log a generic "khong lay duoc
            # tieu de" with no diagnosable cause. Truncate hard: youtube's
            # geo-block error message appends a 200+ country list that would
            # otherwise flood the log window and push everything else out.
            err_lines = [l.strip() for l in (stderr_data or '').splitlines() if l.strip()]
            last_err = err_lines[-1] if err_lines else 'Unknown error'
            if len(last_err) > 200:
                last_err = last_err[:200] + '...'
            self._log(f"yt-dlp loi ({video_id}): {last_err}", 'err')
            return None

        lines = [l.strip() for l in stdout_data.split('\n') if l.strip()]
        if len(lines) < 2:
            return None
        return lines[1]

    def _yt_cleanup_partial(self, output_dir, title_safe):
        """Remove leftover yt-dlp intermediate files after a failed download/mux
        (observed in practice: `<title>.f397.mp4`, `<title>.f140.m4a`,
        `<title>.temp.mp4`). Only removes files whose name is exactly
        `<title_safe>.` followed by a yt-dlp fragment tag (`f<digits>.<ext>`) or
        `temp.<ext>` — never the final output file, and never the `.jpg`
        thumbnail (kept even when the video itself failed).

        Uses os.listdir() + a plain string/regex match instead of glob() on
        purpose: title_safe is not glob-escaped and Youtube titles routinely
        contain `[`, `]`, `*`, `?` (glob metacharacters) that `_safe_filename()`
        does not strip, which would make glob.glob() match incorrectly or miss
        files entirely.
        """
        try:
            entries = os.listdir(output_dir)
        except OSError:
            return
        prefix = title_safe + '.'
        for name in entries:
            if not name.startswith(prefix):
                continue
            rest = name[len(prefix):]
            if rest.lower().endswith('.jpg'):
                continue
            if not (re.match(r'^f\d+\.', rest) or rest.lower().startswith('temp.')):
                continue
            full_path = os.path.join(output_dir, name)
            try:
                os.remove(full_path)
                self._log(f"Da xoa file rac: {name}", 'info')
            except OSError:
                pass

    def _yt_download_one(self, idx, item, settings):
        """Download one Youtube video/audio (§5.5/§5.6) and parse its progress (§7).

        item: {'video_id', 'url', 'title', 'title_safe'}
        settings: dict with output_dir, yt_format, fmt_selector, format_sort,
                  write_thumbnail, concurrent_fragments, retries,
                  fragment_retries, socket_timeout, mp3_quality, player_client,
                  js_runtimes
        Returns True/False. Logs its own errors (returncode != 0, missing output file).
        Thumbnail missing is logged as 'info' and does NOT fail the row.
        On failure (bad returncode or missing output file), cleans up any
        leftover partial/fragment files left behind by yt-dlp/ffmpeg.
        """
        title = item['title']
        title_safe = item['title_safe']
        output_dir = settings['output_dir']
        yt_format = settings['yt_format']
        write_thumbnail = settings['write_thumbnail']
        player_client = settings.get('player_client')
        js_runtimes = settings.get('js_runtimes')
        ext = 'mp3' if yt_format == 'MP3' else 'mp4'
        out_path = os.path.join(output_dir, f"{title_safe}.{ext}")
        out_template = os.path.join(output_dir, f"{title_safe}.%(ext)s")

        # --encoding utf-8: khop voi encoding='utf-8' khi doc stdout/stderr ben duoi.
        # Thieu no thi moi dong log/loi co ky tu ngoai ASCII deu bi bop meo (xem
        # ghi chu day du o _yt_fetch_title).
        cmd = [self.ytdlp_path, "--no-playlist", "--newline", "--no-warnings",
               "--encoding", "utf-8"]
        if yt_format == 'MP3':
            cmd += [
                "-f", "ba/b",
                "-x", "--audio-format", "mp3",
                "--audio-quality", str(settings['mp3_quality']),
            ]
        else:
            cmd += [
                "-f", settings['fmt_selector'],
                "--merge-output-format", "mp4",
                "--remux-video", "mp4",
            ]
            # Uu tien H.264 khi CUNG do phan giai. Xem ly do day du o
            # _yt_build_format: chuoi -f chi loc, khong xep hang duoc.
            # KHONG truyen cho MP3: cac truong res/fps/vcodec deu vo nghia
            # voi luong chi co tieng.
            fmt_sort = settings.get('format_sort')
            if fmt_sort:
                cmd += ["-S", fmt_sort]
        if player_client:
            cmd += ["--extractor-args", f"youtube:player_client={player_client}"]
        if js_runtimes:
            cmd += ["--js-runtimes", js_runtimes]
        # ADR-014: chi tai mot doan. Do that (video 3h49m): doan bat dau o gio
        # thu 3 mat 103 giay, NHANH HON doan dau video - tuc no nhay thang toi
        # diem cat chu khong doc luot tu dau, nen bang thong duoc tiet kiem that.
        # Luu y: co nay chuyen viec tai sang ffmpeg thay vi bo tai cua yt-dlp,
        # nen tien do KHONG con parse duoc tu dong '[download] xx%'.
        section = settings.get('section')
        if section:
            start_s, end_s = section
            cmd += ["--download-sections", f"*{start_s}-{end_s}"]
        else:
            # Chi ap dung khi tai TRON video: buoc yt-dlp tai file bang nhieu
            # doan HTTP range song song (so luong = --concurrent-fragments)
            # thay vi mot luong duy nhat. Voi --download-sections, ffmpeg tu
            # tai thang qua HTTP (xem ghi chu o _run_youtube_download), co
            # nay khong co tac dung nen KHONG truyen de tranh gay hieu nham.
            cmd += ["--http-chunk-size", settings['http_chunk_size']]
        cmd += [
            "--ffmpeg-location", self.ffmpeg_path,
            "--concurrent-fragments", str(settings['concurrent_fragments']),
            "--retries", str(settings['retries']),
            "--fragment-retries", str(settings['fragment_retries']),
            "--socket-timeout", str(settings['socket_timeout']),
        ]
        if write_thumbnail:
            cmd += ["--write-thumbnail", "--convert-thumbnails", "jpg"]
        cmd += ["-o", out_template, item['url']]

        # YouTube doi khi cham dut giua chung mot batch lon bang "Sign in to
        # confirm you're not a bot" - khong phai video bi chan vinh vien, ma la
        # co che chong bot tam thoi kich hoat do goi nhieu yeu cau lien tiep tu
        # cung dia chi mang (do that: 30/32 video OK, dung 2 video cuoi dinh loi
        # nay). Retry lai SAU MOT KHOANG NGHI thuong qua duoc ngay - khac voi cac
        # loi khac (codec khong ho tro, video rieng tu...) retry lai vo ich nen
        # KHONG áp dung o day.
        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding='utf-8',
                    errors='replace',
                    creationflags=subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS,
                )
            except Exception as e:
                self._log(f"Loi spawn yt-dlp: {e}", 'err')
                return False

            self._current_procs.append(proc)

            # §7: two-download-pass progress mapping (video pass 0-70%, audio pass 70-88%),
            # post-processing (merge/remux/extract-audio) bumps to 92%.
            # 100% is never pushed from here — only by the caller once the row is fully done.
            pass_idx = 0
            raw_prev = -1.0
            last_pct = -1
            saw_dl = False          # da thay it nhat mot dong "[download] xx%" chua
            speed_txt = ''          # toc do tai gan nhat, hien canh phan tram
            last_push_t = 0.0       # moc lan day cuoi, de chan lu evaluate_js
            bands = [(0, 70), (70, 88)]
            state_lock = threading.Lock()

            # Do dai doan da yeu cau (giay) - chi co o che do "chi tai mot doan".
            # Dung lam mau so de doi "time=" cua ffmpeg thanh phan tram.
            sec_span = None
            _sec = settings.get('section')
            if _sec:
                try:
                    _span = float(_sec[1]) - float(_sec[0])
                    if _span > 0:
                        sec_span = _span
                except (TypeError, ValueError):
                    sec_span = None

            def push(pct, extra=''):
                """Day tien do len giao dien, dam bao khong bao gio lui.

                Goi tu CA hai luong (stdout cua vong lap chinh, stderr cua drain khi
                o che do cat doan) nen phai khoa - neu khong, hai luong co the doc
                last_pct cu roi ghi de len nhau lam thanh do nhay giat.

                Chan lu evaluate_js: file lon in rat nhieu dong tien do cho cung mot
                con so phan tram. Phan tram tang thi day ngay; chi doi moi toc do
                thi toi da 2 lan/giay.
                """
                nonlocal last_pct, last_push_t
                with state_lock:
                    if pct < last_pct:
                        return
                    now = time.time()
                    if pct == last_pct and (not extra or now - last_push_t < 0.5):
                        return
                    last_pct = pct
                    last_push_t = now
                    payload = json.dumps(extra) if extra else "''"
                    self._js(f"uiApi.updateProcessItem({idx}, {pct}, 'running', {payload})")

            # maxlen: o che do cat doan ffmpeg co the in hang chuc nghin dong; giu het
            # la phi bo nho ma khong duoc gi. Dong tien do bi loai han (xem duoi) nen
            # 400 dong con lai deu la thong tin that.
            stderr_lines = collections.deque(maxlen=400)

            def drain():
                # Toc do o che do cat doan phai TU TINH: ffmpeg khong in san chuoi
                # toc do nhu yt-dlp, chi in "size=" (kB tron). Hai cach don gian deu
                # da thu va deu hong:
                #   - hieu giua hai dong lien tiep: ffmpeg in nhanh hon toc do file
                #     phinh -> ra 0 qua nua so lan;
                #   - cua so 1 giay: van ra 0 xen ke, vi ffmpeg ghi dia theo cum chu
                #     khong deu (do that: 0 / 0 / 243 / 243 / 0 / 0 KiB/s).
                # Cua so truot 5 giay lam phang duoc do gian doan do ma van bam theo
                # tinh hinh mang hien tai, khac voi trung binh tich luy tu dau.
                ff_samples = collections.deque()   # (moc thoi gian, kB)
                ff_speed = ''
                for line in proc.stderr:
                    if RE_FF_PROGRESS_LINE.match(line.lstrip()):
                        # Chi co y nghia o che do cat doan; o che do thuong ffmpeg chi
                        # chay luc ghep nen vai dong nay khong dai dien cho gi.
                        if sec_span:
                            mt = RE_FF_TIME.search(line)
                            if mt:
                                done = (int(mt.group(1)) * 3600 + int(mt.group(2)) * 60
                                        + float(mt.group(3)))
                                pct = int(min(done / sec_span, 1.0) * 88)
                                ms = RE_FF_SIZE.search(line)
                                now = time.time()
                                if ms:
                                    kb = int(ms.group(1))
                                    ff_samples.append((now, kb))
                                    while ff_samples and now - ff_samples[0][0] > 5.0:
                                        ff_samples.popleft()
                                    span = now - ff_samples[0][0]
                                    if len(ff_samples) >= 2 and span >= 1.0:
                                        rate = (kb - ff_samples[0][1]) / span
                                        if rate >= 0:
                                            ff_speed = self._fmt_rate_kb(rate)
                                push(pct, ff_speed)
                        continue
                    stderr_lines.append(line)
            t = threading.Thread(target=drain, daemon=True)
            t.start()

            try:
                for line in proc.stdout:
                    if self._stopped:
                        break
                    line = line.strip()
                    if RE_YT_DEST.match(line):
                        # Bat dau mot luong moi (vd: xong video, sang audio).
                        if saw_dl:
                            pass_idx += 1
                        raw_prev = -1.0
                        continue
                    m = RE_YT_DL.match(line)
                    if m:
                        saw_dl = True
                        raw = float(m.group(1))
                        raw_prev = raw
                        lo, hi = bands[min(pass_idx, len(bands) - 1)]
                        pct = int(lo + raw / 100.0 * (hi - lo))
                        msp = RE_YT_SPEED.search(line)
                        if msp:
                            speed_txt = msp.group(1).replace(' ', '')
                        push(pct, speed_txt)
                        continue
                    if RE_YT_POST.match(line):
                        # Chot chan: chi coi la "sap xong" khi da that su tai duoc gi.
                        # O che do cat doan khong co dong [download] xx% nao trong luc
                        # tai, nhung ffmpeg da day tien trinh qua push() roi.
                        if saw_dl or sec_span:
                            push(92)
                proc.wait()
            finally:
                t.join()
                if proc in self._current_procs:
                    self._current_procs.remove(proc)

            if self._stopped:
                self._js(f"uiApi.updateProcessItem({idx}, {max(last_pct, 0)}, 'stopped')")
                # User hitting Stop mid-download is a routine, frequent action (not
                # an edge case) and can leave multi-hundred-MB `.fNNN.mp4`/`.fNNN.m4a`
                # fragments behind — clean them up so they don't silently pile up
                # across repeated run/stop cycles. Guard: only clean up if the final
                # output is NOT already sitting there complete (yt-dlp could have
                # finished writing it a moment before the stop flag was observed) —
                # never delete a valid finished file.
                if not os.path.exists(out_path):
                    self._yt_cleanup_partial(output_dir, title_safe)
                return False

            if proc.returncode != 0:
                # Chi dong CUOI cung thuong la "ffmpeg exited with code 1" - dung
                # nguyen nhan (loi codec, mang, CDN...) nam o vai dong TRUOC do
                # trong stderr cua ffmpeg. Lay 5 dong cuoi thay vi 1 de con doc
                # duoc ly do that; van gioi han do dai vi mot so loi (vd geo-block)
                # tu ket noi ca danh sach 200+ quoc gia vao MOT dong.
                err_blob = ''.join(stderr_lines)
                err_lines = [l.strip() for l in err_blob.splitlines() if l.strip()]
                last_err = ' | '.join(err_lines[-5:]) if err_lines else 'Unknown error'
                if len(last_err) > 500:
                    last_err = last_err[:500] + '...'
                self._yt_cleanup_partial(output_dir, title_safe)
                if 'not a bot' in err_blob.lower() and attempt < max_attempts:
                    wait_s = 5 + random.uniform(0, 5)
                    self._log(
                        f"[{idx + 1}] YouTube nghi ngo bot, doi {wait_s:.0f}s roi thu lai"
                        f" (lan {attempt + 1}/{max_attempts}): {title}", 'info')
                    time.sleep(wait_s)
                    continue
                self._log(f"yt-dlp loi ({title}): {last_err}", 'err')
                self._log(f"[{idx + 1}] Tai fail: {title}", 'err')
                return False

            if not os.path.exists(out_path):
                self._log(f"[{idx + 1}] Khong tim thay file sau khi tai: {title}", 'err')
                self._yt_cleanup_partial(output_dir, title_safe)
                return False

            if write_thumbnail:
                thumb_path = os.path.join(output_dir, f"{title_safe}.jpg")
                if not os.path.exists(thumb_path):
                    self._log(f"[{idx + 1}] Khong co thumbnail .jpg: {title}", 'info')

            # ADR-016 chi sua duoc khi Youtube CO san avc1 o dung do phan giai da
            # chon (video 1080p tro xuong hau nhu luon co, nhung 1440p/2160p —
            # va video HDR o do phan giai cao nhat — thi Youtube khong phuc vu
            # H.264 nao ca, du chon quality gi). Nhung truong hop do van ra VP9
            # trong vo .mp4 - dung duoi, sai ruot, Premiere/NLE cu bao loi dinh
            # dang. Bao ngay o day thay vi de nguoi dung tu phat hien trong
            # Premiere: khong re-encode o day (ADR-016 da do & bac bo huong nay -
            # 9-18 phut + phinh 2.5-3.8 lan MOI FILE), chi tro toi loi ra da co
            # san ("Convert video", dich MP4, skip_same_codec tranh ma hoa lai
            # file da dung codec).
            if yt_format in ('MP4', 'MP4_NOAUDIO'):
                probed = self._probe_spec(out_path)
                v_codec = (probed or {}).get('v_codec')
                if v_codec and v_codec != 'h264':
                    self._log(
                        f"[{idx + 1}] Luu y: file la {v_codec} (khong phai H.264) du duoi"
                        " la .mp4 - Premiere va mot so NLE cu se bao loi dinh dang."
                        " Neu gap loi do, dung chuc nang 'Convert video' (chon dich MP4)"
                        " tren thu muc Output de chuyen sang H.264.", 'info')

            return True

        return False

    def _run_youtube_download(self, params, code):
        self._js("uiApi.setStatus('Dang chuan bi tai Youtube...')")
        self._js("uiApi.setProgress(0, '')")
        self._log("=== Bat dau tai video Youtube ===", 'info')

        yt_links = params.get('ytLinks', '').strip()
        output_dir = params.get('outputDir', '').strip()
        workers = max(1, params.get('workers', 2))
        yt_format = (params.get('ytFormat') or code.get('default_format', 'MP4')).strip().upper()
        yt_quality = (params.get('ytQuality') or code.get('default_quality', 'Best')).strip()

        # ADR-014: tai mot doan thay vi tron video. Bat/tat bang checkbox chu
        # KHONG suy tu "o co rong hay khong": gia tri sot lai trong o la mot cai
        # bay im lang (cat 2 phut hom nay, tuan sau dan 20 link muon tai tron ma
        # quen xoa o -> ca 20 file bi cat, khong co gi bao).
        section_on = bool(str(params.get('ytSectionEnable', '') or '').strip())
        sec_start = sec_dur = None
        if section_on:
            sec_start = self._parse_time_spec(params.get('ytSectionStart', ''))
            sec_dur = self._parse_time_spec(params.get('ytSectionDuration', ''))

        # Checkbox tren UI la nguon quyet dinh. Phan biet "khong tich" voi
        # "khong co truong" bang cach kiem tra khoa CO TON TAI hay khong:
        # getFieldChecked tra '' cho ca hai truong hop, nen neu chi doc gia tri
        # thi mot phien ban UI cu (chua co checkbox) se am tham TAT anh bia.
        # Khoa vang mat -> quay ve mac dinh cua preset (hanh vi cu).
        _wt = params.get('ytWriteThumbnail')
        if _wt is None:
            write_thumbnail = code.get('write_thumbnail', True)
        else:
            write_thumbnail = bool(str(_wt).strip())
        skip_existing = code.get('skip_existing', True)
        concurrent_fragments = int(code.get('concurrent_fragments', 4))
        # Chi co tac dung khi tai TRON video (khong --download-sections): buoc
        # yt-dlp cat file thanh cac doan co kich thuoc nay roi tai SONG SONG
        # (so luong doan dong thoi = concurrent_fragments o tren) thay vi mot
        # luong HTTP duy nhat. Khi co --download-sections, ffmpeg tu tai
        # thang qua HTTP range (-ss/-t), khong di qua downloader cua yt-dlp
        # nen co nay vo tac dung - xem cho dung no o _yt_download_one.
        http_chunk_size = str(code.get('http_chunk_size', '10M'))
        retries = int(code.get('retries', 3))
        fragment_retries = int(code.get('fragment_retries', 10))
        socket_timeout = int(code.get('socket_timeout', 30))
        mp3_quality = str(code.get('mp3_quality', '0'))
        max_filename_len = int(code.get('max_filename_len', 150))
        # §Loi 1: Youtube's default player-client rotation (web/web_safari/mweb/
        # tv_simply/android/ios) demands a PO Token and fails every video with
        # "Sign in to confirm you're not a bot". 'android_vr' does not require
        # one. Kept in the preset JSON (not hardcoded) so it can be swapped the
        # moment Youtube breaks android_vr too, with zero rebuild.
        player_client = str(code.get('player_client', 'android_vr') or '').strip()
        # ADR-011/012: tu ~2026-08 Youtube bat giai "n challenge" bang JavaScript
        # o MOI duong tai, nen buoc phai co JS runtime. Mac dinh preset la
        # 'quickjs' -> _resolve_js_runtime() ghep duong dan tuyet doi toi
        # bin/qjs.exe di kem app (bin/ khong nam tren PATH nen chi truyen ten
        # thoi la khong du). De trong -> khong truyen co.
        js_runtimes = self._resolve_js_runtime(code.get('js_runtimes', ''))
        # Thu tu xep hang format khi da loc xong bang -f. Mac dinh uu tien
        # H.264 o CUNG do phan giai, vi Premiere/NLE cu khong doc duoc VP9
        # nam trong vo MP4 (bao "loi dinh dang" du file mp4 hoan toan hop le).
        # De trong -> khong truyen -S -> dong lenh giong het truoc khi co
        # truong nay, nen khong the gay hoi quy.
        format_sort = str(code.get('format_sort', 'res,fps,vcodec:h264') or '').strip()
        # ADR-013: PHA LAY TIEU DE dung client RIENG voi pha tai.
        # Hai client hong o hai cho khac nhau (do that 2026-08-18):
        #   android_vr    lay tieu de OK  | tai du lieu 403
        #   web_embedded  lay tieu de bi chan bot khi goi nhieu lan | tai OK
        # Ghep ca hai viec vao mot client thi cho nao cung dinh mot nua. Pha lay
        # tieu de ban N lan goi lien tiep (1 link = 1 lan) nen no la cho de dinh
        # rate-limit nhat - dung client 'app' cho no. Pha tai chi can client tai
        # duoc. De trong -> dung luon player_client (hanh vi cu).
        meta_player_client = str(
            code.get('metadata_player_client', 'android_vr') or '').strip() or player_client

        if not yt_links:
            self._log("Chua nhap link Youtube.", 'err')
            return
        if not output_dir:
            self._log("Chua chon folder Output.", 'err')
            return
        if not os.path.exists(self.ytdlp_path):
            self._log("Khong tim thay yt-dlp.exe (vao Cap nhat yt-dlp).", 'err')
            return
        if not os.path.exists(self.ffmpeg_path):
            self._log("Khong tim thay ffmpeg.exe", 'err')
            return
        if yt_format not in ('MP4', 'MP3', 'MP4_NOAUDIO'):
            self._log(f"Dinh dang khong hop le: {yt_format}", 'err')
            return
        if section_on:
            if sec_start is None:
                self._log(
                    f"Moc bat dau khong hop le: {params.get('ytSectionStart', '')!r}"
                    " (vd 2:00 hoac 1:30:00 hoac so giay)", 'err')
                return
            if sec_dur is None or sec_dur <= 0:
                self._log(
                    f"Thoi luong khong hop le: {params.get('ytSectionDuration', '')!r}"
                    " (vd 2:00 hoac 1:30:00 hoac so giay)", 'err')
                return
        if yt_format in ('MP4', 'MP4_NOAUDIO') and yt_quality not in YT_QUALITIES:
            self._log(f"Chat luong khong hop le: {yt_quality}", 'err')
            return

        os.makedirs(output_dir, exist_ok=True)

        # §5.1: parse + dedupe by video_id, always rebuild the canonical URL
        items = []
        seen_ids = set()
        for raw_line in yt_links.split('\n'):
            line = raw_line.strip()
            if not line:
                continue
            vid = self._yt_extract_video_id(line)
            if not vid:
                self._log(f"Bo qua dong khong phai link Youtube: {line}", 'info')
                continue
            canonical_url = f"https://www.youtube.com/watch?v={vid}"
            if vid in seen_ids:
                self._log(f"Bo qua link trung: {canonical_url}", 'info')
                continue
            seen_ids.add(vid)
            items.append({'video_id': vid, 'url': canonical_url})

        if not items:
            self._log("Khong co link hop le.", 'err')
            return

        total = len(items)
        self._log(f"Tim thay {total} link | {workers} luong | {yt_format} {yt_quality}", 'info')
        if section_on:
            # In ra CACH APP DA HIEU truoc khi tai byte nao. So tran la cho de
            # hieu nham nhat ('2' = 2 giay chu khong phai 2 phut) nen phai cho
            # nguoi dung thay ngay o dong dau, khong de ho doi tai xong moi biet.
            self._log(
                f"Chi tai mot doan: tu {self._fmt_seconds(sec_start)} "
                f"({int(sec_start)} giay), dai {self._fmt_seconds(sec_dur)} "
                f"({int(sec_dur)} giay) -> het o {self._fmt_seconds(sec_start + sec_dur)}."
                " Ap dung cho moi link.", 'info')
            self._log(
                "Doan cat luon dai hon yeu cau vai giay (chi cat duoc o khung chinh,"
                " khong ma hoa lai).", 'info')

        # §5.2/§9(a): pre-pass fetch titles in parallel, before initProcessTable
        self._js("uiApi.setStatus('Dang lay tieu de video...')")
        title_count = [0]

        # ADR-015: uu tien Youtube Data API v3 neu nguoi dung da luu khoa.
        # 1 lan goi cho toi 50 link, khong dinh chong bot. Nhung id API khong
        # tra ve (video rieng tu / da xoa / khoa bi tu choi) van roi xuong
        # duong yt-dlp ben duoi, nen day la lop TANG THEM chu khong thay the.
        api_titles = {}
        api_key = self._load_yt_api_key()
        if api_key:
            all_ids = [it['video_id'] for it in items]
            n_calls = (len(all_ids) + 49) // 50
            self._log(f"Dung API Youtube lay tieu de: {len(all_ids)} link trong"
                      f" {n_calls} lan goi.", 'info')
            api_titles = self._yt_fetch_titles_api(all_ids, api_key, socket_timeout)
            missing = len(all_ids) - len(api_titles)
            if missing > 0:
                self._log(f"API tra ve {len(api_titles)}/{len(all_ids)} tieu de;"
                          f" {missing} link con lai se thu bang yt-dlp.", 'info')

        def fetch_one(i, it):
            if self._stopped:
                return i, it['video_id']
            title = api_titles.get(it['video_id'])
            if title:
                with self._lock:
                    title_count[0] += 1
                    k = title_count[0]
                self._log(f"Lay tieu de: {k}/{total}", 'info')
                return i, title
            title = self._yt_fetch_title(it['video_id'], socket_timeout,
                                         meta_player_client, js_runtimes)
            # ADR-013: hong thi thu client CON LAI mot lan truoc khi bo cuoc.
            # Hai client hong o hai co che khac nhau (mot doi PO Token, mot bi
            # chan bot theo tan suat) nen it khi hong cung luc. Chan-bot lai la
            # thu KHONG on dinh - phu thuoc lich su truy van tich luy tu dia chi
            # mang - nen mot lan thu lai bang duong khac la re va dang gia.
            if not title and player_client and player_client != meta_player_client:
                title = self._yt_fetch_title(it['video_id'], socket_timeout,
                                             player_client, js_runtimes)
                if title:
                    self._log(f"[{i + 1}] Lay tieu de bang {player_client} (du phong).", 'info')
            if not title:
                title = it['video_id']
                self._log(f"[{i + 1}] Khong lay duoc tieu de, dung ID: {it['video_id']}", 'info')
            with self._lock:
                title_count[0] += 1
                k = title_count[0]
            self._log(f"Lay tieu de: {k}/{total}", 'info')
            return i, title

        titles = [None] * total
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = {}
            for i, it in enumerate(items):
                if self._stopped:
                    break
                futures[ex.submit(fetch_one, i, it)] = i
            for fut in as_completed(futures):
                i = futures[fut]
                try:
                    i, title = fut.result()
                except Exception:
                    title = items[i]['video_id']
                titles[i] = title

        # §5.3: sanitize filenames, dedupe title collisions within this batch
        used_names = set()
        for i, it in enumerate(items):
            title = titles[i] if titles[i] is not None else it['video_id']
            it['title'] = title
            safe = self._safe_filename(title)
            safe = safe.replace('%', '_')
            safe = safe.strip().rstrip('. ')
            safe = safe[:max_filename_len]
            if not safe:
                safe = it['video_id']
            # ADR-014: ten file mang KHOANG THOI GIAN va co KHONG-TIENG.
            # BAT BUOC, khong phai trang tri: khong co chung thi ban cat va ban
            # day du (hoac ban khong tieng va ban co tieng) TRUNG TEN HET NHAU,
            # ma app lai bo qua file da ton tai -> tai ban nay roi doi y muon ban
            # kia thi app bo qua, nguoi dung tuong minh da co thu vua xin.
            # Dung '_' thay ':' vi ':' la ky tu cam trong ten file Windows.
            tag = ''
            if section_on:
                tag += (f" [{self._fmt_seconds(sec_start).replace(':', '_')}"
                        f"+{self._fmt_seconds(sec_dur).replace(':', '_')}]")
            if yt_format == 'MP4_NOAUDIO':
                tag += ' [khong tieng]'
            if tag:
                safe = safe[:max(1, max_filename_len - len(tag))] + tag
            if safe in used_names:
                new_name = f"{safe}_{it['video_id']}"
                self._log(f"Trung ten, doi thanh: {new_name}", 'info')
                safe = new_name
            used_names.add(safe)
            it['title_safe'] = safe

        run_items = self._begin_batch([it['title'] for it in items])
        total = len(run_items)
        if self._retry_mode == 'all' and skip_existing:
            self._log("Luu y: file da co van bi bo qua. Xoa file cu neu muon tai lai.", 'info')

        ok_count = [0]
        done_count = [0]

        def update(idx, success):
            with self._lock:
                if success:
                    ok_count[0] += 1
                done_count[0] += 1
                d = done_count[0]
            self._mark_row(idx, success)
            self._js(f"uiApi.setProgress({int(d / total * 100)}, '{d}/{total}')")
            self._js(f"uiApi.setStatus('Dang tai... {d}/{total} video')")

        settings = {
            'output_dir': output_dir,
            'yt_format': yt_format,
            'fmt_selector': self._yt_build_format(yt_quality, yt_format),
            'format_sort': format_sort,
            'write_thumbnail': write_thumbnail,
            'concurrent_fragments': concurrent_fragments,
            'http_chunk_size': http_chunk_size,
            'retries': retries,
            'fragment_retries': fragment_retries,
            'socket_timeout': socket_timeout,
            'mp3_quality': mp3_quality,
            'player_client': player_client,
            'js_runtimes': js_runtimes,
            # None = tai tron video (khong truyen --download-sections).
            'section': (sec_start, sec_start + sec_dur) if section_on else None,
        }

        def process_one(idx, it):
            self._js(f"uiApi.updateProcessItem({idx}, 0, 'running')")
            if self._stopped:
                return False
            ext = 'mp3' if yt_format == 'MP3' else 'mp4'
            out_path = os.path.join(output_dir, f"{it['title_safe']}.{ext}")

            if skip_existing and os.path.exists(out_path):
                self._log(f"[{idx + 1}] Da co, bo qua: {it['title']}", 'info')
                return True

            try:
                return self._yt_download_one(idx, it, settings)
            except Exception as e:
                self._log(f"[{idx + 1}] LOI: {e}", 'err')
                return False

        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = {}
            for idx, _lb in run_items:
                if self._stopped:
                    break
                futures[ex.submit(process_one, idx, items[idx])] = idx
            for fut in as_completed(futures):
                idx = futures[fut]
                try:
                    success = fut.result()
                except Exception as e:
                    self._log(f"[{idx + 1}] LOI: {e}", 'err')
                    success = False
                update(idx, success)

        self._log(f"=== Hoan thanh: {ok_count[0]}/{total} video ===", 'ok')
        self._js(f"uiApi.setStatus('Xong! {ok_count[0]}/{total} video.')")

    # ── Youtube Thumbnail (ADR-008) ──

    @staticmethod
    def _yt_thumb_normalize_channel(url):
        """Normalize a Youtube channel/playlist URL before --flat-playlist listing.

        Channel URLs (/@name, /channel/ID, /c/name, /user/name) get '/videos'
        appended UNLESS the URL already ends on a recognised tab
        (YT_CHANNEL_TABS) - this is what keeps a channel link to "N most
        recent videos" and deliberately excludes Shorts (a separate tab).
        Playlist URLs (RE_YT_PLAYLIST) are returned unchanged. The query
        string is dropped when '/videos' gets appended to a bare channel URL
        (a stray '?si=...' share param would otherwise be carried into the
        tab path and confuse yt-dlp).
        """
        if RE_YT_PLAYLIST.search(url):
            return url
        if not RE_YT_CHANNEL.search(url):
            return url
        base = url.split('?', 1)[0].split('#', 1)[0].rstrip('/')
        last_seg = base.rsplit('/', 1)[-1].lower()
        if last_seg in YT_CHANNEL_TABS:
            return base
        return base + '/videos'

    def _yt_thumb_list_channel(self, url, limit, socket_timeout, player_client,
                               js_runtimes=None):
        """List up to `limit` videos of a channel/playlist URL via a single
        `yt-dlp --flat-playlist` call (one process for the WHOLE channel -
        cheap compared to per-video title fetches).

        Returns list[(video_id, title)] in Youtube's own order (a channel's
        '/videos' tab = newest first). On total failure (spawn error, or
        non-zero returncode with zero parsed pairs), logs the error and
        returns [] - the caller treats that as "this link contributed 0
        videos" and keeps processing the rest of the batch (per-link
        isolation, batch does not stop).
        """
        if self._stopped:
            return []
        cmd = [self.ytdlp_path,
               "--flat-playlist", "--no-warnings", "--ignore-errors",
               "--encoding", "utf-8",
               "--socket-timeout", str(socket_timeout),
               "--playlist-end", str(limit),
               "--print", "%(id)s", "--print", "%(title)s"]
        if player_client:
            cmd += ["--extractor-args", f"youtube:player_client={player_client}"]
        if js_runtimes:
            cmd += ["--js-runtimes", js_runtimes]
        cmd.append(url)

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding='utf-8',
                errors='replace',
                creationflags=subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS,
            )
            self._current_procs.append(proc)
            try:
                stdout_data, stderr_data = proc.communicate()
            finally:
                if proc in self._current_procs:
                    self._current_procs.remove(proc)
        except Exception as e:
            self._log(f"Khong liet ke duoc kenh: {url}", 'err')
            self._log(f"yt-dlp loi ({url}): {e}", 'err')
            return []

        # Ghep theo cap (id, title). Cap nao phan id khong khop YT_ID_BARE thi
        # bo cap do - chong lech dong khi --ignore-errors nuot mot entry.
        lines = [l.strip() for l in (stdout_data or '').split('\n') if l.strip()]
        pairs = []
        for i in range(0, len(lines) - 1, 2):
            vid, title = lines[i], lines[i + 1]
            if YT_ID_BARE.match(vid):
                pairs.append((vid, title))

        if proc.returncode != 0 and not pairs:
            err_lines = [l.strip() for l in (stderr_data or '').splitlines() if l.strip()]
            last_err = err_lines[-1] if err_lines else 'Unknown error'
            if len(last_err) > 200:
                last_err = last_err[:200] + '...'
            self._log(f"Khong liet ke duoc kenh: {url}", 'err')
            self._log(f"yt-dlp loi ({url}): {last_err}", 'err')

        return pairs

    @staticmethod
    def _yt_thumb_resolve_ladder(code):
        """Resolve + validate `code.get('size_ladder')` (ADR-008 amendment).

        Preset field has REAL effect (wired into _yt_thumb_fetch): the size
        ladder is Youtube's own naming convention, and if Youtube ever
        changes it, editing one line of JSON can save the feature without
        rebuilding the .exe (same spirit as `player_client` in ADR-006).

        Never raises. Returns (ladder, warning):
        - `ladder`: always a non-empty list of KNOWN rung names (members of
          the module constant YT_THUMB_LADDER). Unknown/misspelled entries
          are dropped one-by-one (typo-tolerant - a single bad entry does
          not kill an otherwise-valid custom ladder); duplicates collapsed,
          first occurrence order kept.
        - `warning`: None when 'size_ladder' is absent/empty (normal case -
          most presets do not override it) or fully valid; otherwise a
          short human-readable message for the caller to log, so a typo in
          the preset degrades LOUDLY to the safe default/partial list
          instead of silently doing nothing.
        Field missing, not a list, or every entry invalid -> falls back to
        the full YT_THUMB_LADDER constant (with a warning in the last two
        cases). Field present with SOME valid entries -> returns just the
        valid ones (still typo-tolerant), with a warning listing what got
        dropped.
        """
        raw = code.get('size_ladder')
        if not raw:
            return list(YT_THUMB_LADDER), None
        if not isinstance(raw, list):
            return (list(YT_THUMB_LADDER),
                    "size_ladder trong preset khong phai list, dung mac dinh.")

        seen = set()
        cleaned = []
        dropped = []
        for entry in raw:
            name = entry.strip() if isinstance(entry, str) else None
            if not name:
                dropped.append(repr(entry))
                continue
            if name not in YT_THUMB_LADDER:
                dropped.append(name)
                continue
            if name in seen:
                continue
            seen.add(name)
            cleaned.append(name)

        if not cleaned:
            return (list(YT_THUMB_LADDER),
                    "size_ladder trong preset khong co gia tri hop le nao, dung mac dinh.")
        if dropped:
            return cleaned, f"size_ladder co gia tri la, da bo qua: {', '.join(dropped)}."
        return cleaned, None

    def _yt_thumb_fetch(self, video_id, start_rung, cache_dir, timeout, http_retries, ladder=None):
        """Download the best available thumbnail at-or-below `start_rung` on
        `ladder` (defaults to the module constant YT_THUMB_LADDER when not
        given/empty - see _yt_thumb_resolve_ladder for how a preset's
        'size_ladder' field gets validated into this parameter), straight
        into `cache_dir`.

        Returns (rung, cache_path, width, height) on success, None on total
        failure (every rung on the ladder 404'd/403'd, or a network error
        exhausted all `http_retries` retries).

        BAT BIEN SONG CON (ADR-008, khong duoc doi): CHI 404/403 (va anh
        120x90 gia - Youtube's grey placeholder served instead of a real 404
        for some missing sizes) moi lui bac trong ladder. Loi mang/timeout
        thi thu lai CUNG mot bac toi da `http_retries` lan; het luot ma van
        loi mang -> return None NGAY (khong lui bac). Gop hai loai loi nay se
        khien mang chap chon am tham ha chat luong anh ma user khong biet.
        """
        ladder_list = ladder if ladder else YT_THUMB_LADDER
        try:
            start_idx = ladder_list.index(start_rung)
        except ValueError:
            start_idx = 0
        ladder_slice = ladder_list[start_idx:]
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) RENUP"}

        for rung in ladder_slice:
            if self._stopped:
                return None
            img_url = YT_THUMB_URL.format(vid=video_id, rung=rung)
            req = urllib.request.Request(img_url, headers=headers)

            data = None
            downgrade = False
            for attempt in range(http_retries + 1):
                if self._stopped:
                    return None
                try:
                    with urllib.request.urlopen(req, timeout=timeout) as resp:
                        data = resp.read()
                    break
                except urllib.error.HTTPError as e:
                    if e.code in (403, 404):
                        downgrade = True
                        break
                    # HTTP loi khac (vd 5xx) -> coi nhu loi mang, thu lai CUNG rung
                    if attempt >= http_retries:
                        return None
                except (urllib.error.URLError, OSError):
                    if attempt >= http_retries:
                        return None

            if downgrade:
                continue
            if data is None:
                return None

            cache_path = os.path.join(cache_dir, f"{video_id}_{rung}.jpg")
            part_path = f"{cache_path}.{uuid.uuid4().hex}.part"
            try:
                with open(part_path, 'wb') as f:
                    f.write(data)
                os.replace(part_path, cache_path)
            except OSError:
                if os.path.exists(part_path):
                    try:
                        os.remove(part_path)
                    except OSError:
                        pass
                return None

            try:
                with Image.open(cache_path) as im:
                    w, h = im.size
            except Exception:
                try:
                    os.remove(cache_path)
                except OSError:
                    pass
                return None

            if (w, h) == (120, 90) and rung != 'default':
                # Youtube doi khi tra anh xam placeholder thay vi 404 that su
                # cho mot size khong ton tai -> coi nhu 404, lui bac.
                try:
                    os.remove(cache_path)
                except OSError:
                    pass
                continue

            return (rung, cache_path, w, h)

        return None

    def _yt_thumb_load_work(self, links_text, size, count, code, workers=2):
        """Than luong nen cua nut Load (§6.8). Bat buoc co finally: dat
        self.is_running = False + uiApi.setRunning(false). KHONG goi
        _end_run() - function nay dung ngoai co che retry cua ADR-007 (§7.4),
        de _batch_labels tiep tuc la None thi _end_run() trong finally cua
        run()._work() (chi ap dung cho buoc Download, khong ap dung o day vi
        Load khong di qua run()) khong co gi de doc/xoa nham.
        """
        try:
            self._js("uiApi.setStatus('Dang liet ke video...')")
            self._js("uiApi.setProgress(0, '')")
            self._log("=== Bat dau load thumbnail Youtube ===", 'info')

            links_text = (links_text or '').strip()
            if not links_text:
                self._log("Chua nhap link Youtube.", 'err')
                return
            if not os.path.exists(self.ytdlp_path):
                self._log("Khong tim thay yt-dlp.exe (vao Cap nhat yt-dlp).", 'err')
                return

            default_limit = int(code.get('default_channel_limit', 50))
            max_limit = int(code.get('max_channel_limit', 200))
            try:
                limit = int(count)
            except (TypeError, ValueError):
                limit = default_limit
            if limit < 1:
                limit = default_limit
            if limit > max_limit:
                limit = max_limit
                self._log(f"So video moi kenh vuot gioi han, dung {max_limit}.", 'info')

            default_size = code.get('default_size', 'maxresdefault')
            start_rung = size if size in YT_THUMB_LADDER else default_size
            if start_rung not in YT_THUMB_LADDER:
                start_rung = 'maxresdefault'

            # size_ladder (preset field, ADR-008 amendment): validated once
            # per Load, then reused for every _yt_thumb_fetch() call below.
            # Missing/empty field -> silent fallback (normal case). Present
            # but malformed -> logged loudly so a typo does not silently do
            # nothing (per coordinator review).
            ladder, ladder_warning = self._yt_thumb_resolve_ladder(code)
            if ladder_warning:
                self._log(ladder_warning, 'info')

            player_client = str(code.get('player_client', 'android_vr') or '').strip()
            # ADR-011/012 - xem ghi chu day du o _run_youtube_download. Chuc nang
            # nay mac dinh de trong: no chi dung yt-dlp cho metadata (liet ke kenh
            # + lay tieu de), ca hai van chay voi android_vr, nen khong can runtime.
            js_runtimes = self._resolve_js_runtime(code.get('js_runtimes', ''))
            socket_timeout = int(code.get('socket_timeout', 30))
            http_retries = int(code.get('http_retries', 2))
            max_filename_len = int(code.get('max_filename_len', 150))
            preview_mode = code.get('preview_mode', 'file')
            preview_max_width = int(code.get('preview_max_width', 320))
            workers = max(1, int(workers or 2))
            cache_dir = self._yt_thumb_dir

            # §6.8 buoc 5: don sach luoi + cache TRUOC khi lam bat cu viec gi
            # khac. Xoa cache khong bao gio duoc fatal (Windows co the con giu
            # file ma webview vua hien thi) - boc try/except, bo qua loi.
            self._js("uiApi.ytThumbClearGrid()")
            self._yt_thumb_items = []
            self._yt_thumb_index = {}
            try:
                os.makedirs(cache_dir, exist_ok=True)
                for fname in os.listdir(cache_dir):
                    try:
                        os.remove(os.path.join(cache_dir, fname))
                    except OSError:
                        pass
            except OSError:
                pass

            # §6.8 buoc 6: phan loai tung dong (§6.3). Video le duoc gom lai
            # de lay tieu de mot lan song song sau vong lap (§6.5); link
            # kenh/playlist duoc liet ke ngay (mot lenh yt-dlp cho ca kenh).
            entries = []          # [(video_id, title), ...] thu tu catalogue
            seen_ids = set()

            def add_entry(vid, title):
                if vid in seen_ids:
                    self._log(f"Bo qua link trung: {vid}", 'info')
                    return
                seen_ids.add(vid)
                entries.append((vid, title))

            single_video_ids = []

            for raw_line in links_text.split('\n'):
                if self._stopped:
                    break
                line = raw_line.strip()
                if not line:
                    continue
                vid = self._yt_extract_video_id(line)
                if vid:
                    # Video le (kem &list= van roi vao day - khong bao gio
                    # bung playlist tu mot link video, nhat quan ADR-005).
                    single_video_ids.append(vid)
                    continue
                if RE_YT_CHANNEL.search(line) or RE_YT_PLAYLIST.search(line):
                    normalized = self._yt_thumb_normalize_channel(line)
                    pairs = self._yt_thumb_list_channel(normalized, limit, socket_timeout,
                                                        player_client, js_runtimes)
                    self._log(f"Kenh: {line} -> {len(pairs)} video", 'info')
                    for vid2, title2 in pairs:
                        add_entry(vid2, title2)
                    continue
                self._log(f"Bo qua dong khong phai link Youtube: {line}", 'info')

            # §6.5: lay tieu de cho video le, song song bang chinh `workers`.
            if single_video_ids and not self._stopped:
                self._js("uiApi.setStatus('Dang lay tieu de video...')")
                total_single = len(single_video_ids)
                title_count = [0]

                def fetch_one(i, vid):
                    if self._stopped:
                        return vid, None
                    title = self._yt_fetch_title(vid, socket_timeout, player_client,
                                                 js_runtimes)
                    if not title:
                        title = vid
                        self._log(f"[{i + 1}] Khong lay duoc tieu de, dung ID: {vid}", 'info')
                    with self._lock:
                        title_count[0] += 1
                        k = title_count[0]
                    self._log(f"Lay tieu de: {k}/{total_single}", 'info')
                    return vid, title

                results_by_id = {}
                with ThreadPoolExecutor(max_workers=workers) as ex:
                    futures = []
                    for i, vid in enumerate(single_video_ids):
                        if self._stopped:
                            break
                        futures.append(ex.submit(fetch_one, i, vid))
                    for fut in as_completed(futures):
                        try:
                            vid, title = fut.result()
                        except Exception:
                            continue
                        if title is not None:
                            results_by_id[vid] = title

                for vid in single_video_ids:
                    if vid in results_by_id:
                        add_entry(vid, results_by_id[vid])

            if not entries:
                self._log("Khong co video nao de load.", 'err')
                return

            # §6.8 buoc 8: sanitize ten file - giong het ADR-005 §5.3, khong
            # sua _safe_filename() (dung chung voi Claim Tiktok + Youtube Download).
            used_names = set()
            items = []
            for vid, title in entries:
                safe = self._safe_filename(title)
                safe = safe.replace('%', '_')
                safe = safe.strip().rstrip('. ')
                safe = safe[:max_filename_len]
                if not safe:
                    safe = vid
                if safe in used_names:
                    new_name = f"{safe}_{vid}"
                    self._log(f"Trung ten, doi thanh: {new_name}", 'info')
                    safe = new_name
                used_names.add(safe)
                items.append({
                    'video_id': vid, 'title': title, 'title_safe': safe,
                    'requested_rung': start_rung,
                })

            n = len(items)
            self._log(f"Tim thay {n} video | {workers} luong | co {start_rung}", 'info')
            self._js("uiApi.setStatus('Dang tai anh xem truoc...')")

            # §6.8 buoc 10-11: tai anh song song, day sang JS THEO THU TU
            # CATALOGUE (khong phai thu tu hoan thanh) bang mot con tro, theo
            # tung lo YT_THUMB_PUSH_CHUNK item de luoi hien dan thay vi dung
            # im toi cuoi.
            results = [None] * n     # None=chua xong, False=fail, dict=thanh cong
            pending = []
            cursor = [0]
            done = [0]
            ok = [0]

            def flush():
                if not pending:
                    return
                payload = list(pending)
                pending.clear()
                self._js(f"uiApi.ytThumbAddItems({json.dumps(payload, ensure_ascii=False)})")

            def fetch_thumb(item):
                if self._stopped:
                    return None
                return self._yt_thumb_fetch(item['video_id'], start_rung, cache_dir,
                                             socket_timeout, http_retries, ladder)

            with ThreadPoolExecutor(max_workers=workers) as ex:
                futures = {}
                for i, item in enumerate(items):
                    if self._stopped:
                        break
                    futures[ex.submit(fetch_thumb, item)] = i
                for fut in as_completed(futures):
                    i = futures[fut]
                    item = items[i]
                    try:
                        r = fut.result()
                    except Exception:
                        r = None

                    with self._lock:
                        done[0] += 1
                        d = done[0]

                    if r is None:
                        self._log(f"[{i + 1}] Khong tai duoc thumbnail: {item['title']}", 'err')
                        results[i] = False
                    else:
                        rung, cache_path, w, h = r
                        downgraded = (rung != start_rung)
                        if downgraded:
                            self._log(
                                f"[{i + 1}] Khong co co {start_rung}, dung {rung}: {item['title']}",
                                'info')
                        if preview_mode == 'data':
                            try:
                                with Image.open(cache_path) as im:
                                    im = im.copy()
                                im.thumbnail((preview_max_width, preview_max_width * 10))
                                buf = io.BytesIO()
                                im.convert('RGB').save(buf, 'JPEG', quality=70)
                                preview_url = ('data:image/jpeg;base64,'
                                               + base64.b64encode(buf.getvalue()).decode('ascii'))
                            except Exception:
                                preview_url = ''
                        else:
                            preview_url = 'file:' + urllib.request.pathname2url(cache_path)

                        full_item = dict(item)
                        full_item.update({
                            'rung': rung, 'downgraded': downgraded,
                            'width': w, 'height': h,
                            'cache_path': cache_path, 'preview_url': preview_url,
                        })
                        results[i] = full_item
                        with self._lock:
                            ok[0] += 1

                    # Day con tro qua moi vi tri da co ket qua, gom item
                    # thanh cong vao pending (item fail khong vao luoi).
                    while cursor[0] < n and results[cursor[0]] is not None:
                        r_i = results[cursor[0]]
                        if r_i is not False:
                            self._yt_thumb_items.append(r_i)
                            self._yt_thumb_index[r_i['video_id']] = r_i
                            pending.append({
                                'id': r_i['video_id'], 'title': r_i['title'],
                                'previewUrl': r_i['preview_url'],
                                'width': r_i['width'], 'height': r_i['height'],
                                'rung': r_i['rung'], 'downgraded': r_i['downgraded'],
                            })
                        cursor[0] += 1

                    finished_all = (cursor[0] >= n)
                    if len(pending) >= YT_THUMB_PUSH_CHUNK or (finished_all and pending):
                        flush()
                        self._js(f"uiApi.ytThumbSetCount({len(self._yt_thumb_items)}, {n})")
                        self._js(f"uiApi.setProgress({int(d / n * 100)}, '{d}/{n}')")

            flush()

            self._log(f"=== Da load {ok[0]}/{n} anh ===", 'ok')
            self._js(f"uiApi.setStatus('Da load {ok[0]}/{n} anh.')")
            self._js(f"uiApi.setProgress(100, '{ok[0]}/{n}')")
        except Exception as e:
            self._log(f"LOI: {e}", 'err')
        finally:
            self.is_running = False
            self._js("uiApi.setRunning(false)")

    def _run_yt_thumbnail(self, params, code):
        """Buoc Download (§7): 100% cuc bo, KHONG mang. Doc catalogue
        `self._yt_thumb_items` (ghi boi lan Load gan nhat) + tap id da chon
        (`ytThumbSelected`), convert file cache co san bang Pillow -> Output.
        Ghi de neu trung ten (KHONG skip-existing, ADR-008 Quyet dinh 10).

        Dung ngoai ADR-007 co chu y (§7.4/I7): KHONG goi _begin_batch /
        _mark_row / initProcessTable / updateProcessItem.
        """
        self._log("=== Bat dau tai thumbnail ===", 'info')
        self._js("uiApi.setProgress(0, '')")

        output_dir = (params.get('outputDir') or '').strip()
        workers = max(1, params.get('workers', 2))
        fmt = (params.get('ytThumbFormat') or code.get('default_format', 'JPG')).strip().upper()
        sel_raw = params.get('ytThumbSelected', '') or ''

        if not output_dir:
            self._log("Chua chon folder Output.", 'err')
            return
        if not self._yt_thumb_items:
            self._log("Chua load anh nao. Nhan Load truoc.", 'err')
            return
        if fmt not in YT_THUMB_FORMATS:
            self._log(f"Dinh dang khong hop le: {fmt}", 'err')
            return

        sel_ids = {s.strip() for s in sel_raw.split(',') if s.strip()}
        # Thu tu catalogue, KHONG phai thu tu user tick. Id la nhung trong
        # catalogue bi bo qua im lang (loc theo self._yt_thumb_items).
        targets = [it for it in self._yt_thumb_items if it['video_id'] in sel_ids]
        if not targets:
            self._log("Chua chon anh nao.", 'err')
            return

        os.makedirs(output_dir, exist_ok=True)

        ext = YT_THUMB_FORMATS[fmt]
        jpg_quality = int(code.get('jpg_quality', 95))
        webp_quality = int(code.get('webp_quality', 95))
        total = len(targets)
        done = [0]
        ok = [0]

        def save_one(idx, item):
            vid = item['video_id']
            title = item['title']
            self._js(f"uiApi.ytThumbSetState('{vid}', 'saving')")

            cache_path = item.get('cache_path', '')
            if not cache_path or not os.path.exists(cache_path):
                self._log(f"[{idx + 1}] Khong con file cache, load lai: {title}", 'err')
                self._js(f"uiApi.ytThumbSetState('{vid}', 'error')")
                return False

            out_path = os.path.join(output_dir, f"{item['title_safe']}{ext}")
            try:
                img = Image.open(cache_path)
                # Cung khoi chuyen mode voi _run_convert_image (dong ~1050).
                if ext in ('.jpg', '.jpeg') and img.mode in ('RGBA', 'P'):
                    bg = Image.new('RGB', img.size, (255, 255, 255))
                    if img.mode == 'P':
                        img = img.convert('RGBA')
                    bg.paste(img, mask=img.split()[3])
                    img = bg
                elif img.mode == 'P':
                    img = img.convert('RGBA' if ext == '.png' else 'RGB')

                save_kwargs = {}
                if ext in ('.jpg', '.jpeg'):
                    save_kwargs = {'quality': jpg_quality, 'optimize': True}
                elif ext == '.webp':
                    save_kwargs = {'quality': webp_quality, 'method': 4}
                elif ext == '.png':
                    save_kwargs = {'optimize': True}

                # Ghi de neu file da ton tai - KHONG skip-existing (khac
                # youtube_download: o day chay lai chi ton ~10ms CPU, khong
                # phai hang GB bang thong).
                img.save(out_path, **save_kwargs)
                self._js(f"uiApi.ytThumbSetState('{vid}', 'done')")
                self._log(f"  OK: {os.path.basename(out_path)}", 'ok')
                return True
            except Exception as e:
                self._log(f"  LOI: {e}", 'err')
                self._js(f"uiApi.ytThumbSetState('{vid}', 'error')")
                return False

        def process_one(idx, item):
            if self._stopped:
                return False
            return save_one(idx, item)

        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = {}
            for idx, item in enumerate(targets):
                if self._stopped:
                    break
                futures[ex.submit(process_one, idx, item)] = idx
            for fut in as_completed(futures):
                idx = futures[fut]
                try:
                    success = fut.result()
                except Exception as e:
                    self._log(f"  LOI: {e}", 'err')
                    success = False
                with self._lock:
                    if success:
                        ok[0] += 1
                    done[0] += 1
                    d = done[0]
                self._js(f"uiApi.setProgress({int(d / total * 100)}, '{d}/{total}')")
                self._js(f"uiApi.setStatus('Dang luu anh... {d}/{total}')")

        self._log(f"=== Hoan thanh: {ok[0]}/{total} anh ===", 'ok')
        self._js(f"uiApi.setStatus('Xong! {ok[0]}/{total} anh.')")

    # ── Auto Update ──

    def _check_update(self):
        import time as _t
        # Wait for UI to fully load
        _t.sleep(3)

        try:
            current = get_version()

            req = urllib.request.Request(GITHUB_API, headers={"User-Agent": "RENUP-Updater"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())

            latest = data.get("tag_name", "").lstrip("v")
            if not latest:
                return

            self._log(f"Kiem tra update: hien tai v{current}, moi nhat v{latest}", 'info')

            cmp = self._ver_cmp(latest, current)
            if cmp > 0:
                download_url = ""
                for asset in data.get("assets", []):
                    name = asset.get("name", "")
                    if UPDATE_ASSET_RE.match(name):
                        download_url = asset.get("browser_download_url", "")
                        break

                if not download_url:
                    # Khong tim thay dung file cai dat -> thoi khong moi cap
                    # nhat. Tha im lang con hon tai nham thu gi do va pha
                    # app cua nguoi dung.
                    self._log(
                        f"Co ban cap nhat v{latest} nhung khong tim thay file cai dat "
                        f"phu hop (RENUP_Setup_v*.exe) trong release. Bo qua.",
                        'info'
                    )
                    return

                self._log(f"Co ban cap nhat moi v{latest}!", 'ok')
                safe_url = download_url.replace("'", "\\'")
                self._js(f"showUpdateDialog('{current}', '{latest}', '{safe_url}')")
            else:
                self._log(f"Dang dung phien ban moi nhat.", 'info')
        except urllib.error.HTTPError as e:
            if e.code == 403:
                self._log("GitHub API rate limit. Thu lai sau.", 'info')
            else:
                self._log(f"API error: {e.code}", 'err')
        except urllib.error.URLError:
            self._log("Khong co ket noi mang.", 'info')
        except Exception as e:
            self._log(f"Loi kiem tra update: {e}", 'err')

    def downloadUpdate(self, url):
        """Tai bo cai dat Inno Setup (RENUP_Setup_v<version>.exe) va tu chay
        no o che do hoan toan im lang - nguoi dung chi nhan 1 nut bam,
        khong phai thao tac gi them (khong Next, khong xac nhan).

        Luong: tai vao thu muc tam he thong (khong phai thu muc cai dat cua
        app - bo cai sap ghi de dung cho o do) -> kiem tra toan ven (kich
        thuoc khop Content-Length + header MZ hop le) -> viet 1 watcher .bat
        (chay bo cai bang "start /wait" de bat duoc ma loi THAT SU cua bo
        cai, roi bao loi bang MessageBox neu ma loi khac 0) -> chi sau khi
        watcher da khoi chay thanh cong moi tu dong (kill ffmpeg dang chay
        + dong cua so) de giai phong khoa file cho bo cai ghi de. Neu bat
        ky buoc nao truoc do that bai, app KHONG dong, khong o trang thai
        nua voi.
        """
        if not url:
            webbrowser.open(f"https://github.com/{GITHUB_REPO}/releases/latest")
            return
        if self.is_running:
            self._log("Dang co tac vu chay. Doi xong hoac nhan Dung truoc khi cap nhat.", 'err')
            return
        if self._update_in_progress:
            self._log("Dang cap nhat, vui long doi.", 'info')
            return

        self._update_in_progress = True
        self._log("=== Dang tai ban cap nhat... ===", 'info')
        self._js("uiApi.setStatus('Dang tai ban cap nhat...')")
        self._js("uiApi.setProgress(0, 'Dang tai...')")

        def _dl():
            import time

            update_dir = os.path.join(tempfile.gettempdir(), 'RENUP_Update')
            asset_name = url.rsplit('/', 1)[-1]
            installer_path = os.path.join(update_dir, asset_name)
            bat_path = None

            # ── Buoc 1: tai bo cai vao thu muc tam + kiem tra toan ven ──
            # KHONG tai vao thu muc cai dat cua app: bo cai sap ghi de
            # chinh thu muc do, tai chong len se tu pha du lieu dang tai.
            try:
                if not UPDATE_ASSET_RE.match(asset_name):
                    raise Exception(f"Ten file tai ve khong hop le: {asset_name}")

                os.makedirs(update_dir, exist_ok=True)

                req = urllib.request.Request(url, headers={"User-Agent": "RENUP-Updater"})
                with urllib.request.urlopen(req, timeout=600) as resp:
                    total_size = int(resp.headers.get('Content-Length', 0))
                    downloaded = 0
                    with open(installer_path, 'wb') as f:
                        while True:
                            chunk = resp.read(65536)
                            if not chunk:
                                break
                            f.write(chunk)
                            downloaded += len(chunk)
                            if total_size > 0:
                                pct = int(downloaded / total_size * 100)
                                mb_dl = downloaded // (1024 * 1024)
                                mb_total = total_size // (1024 * 1024)
                                self._js(f"uiApi.setProgress({pct}, 'Tai: {mb_dl}MB / {mb_total}MB')")
                            else:
                                mb_dl = downloaded // (1024 * 1024)
                                self._js(f"uiApi.setProgress(50, 'Tai: {mb_dl}MB...')")

                # Kiem tra toan ven: file ton tai, kich thuoc hop ly, kich
                # thuoc khop Content-Length, va la file thuc thi Windows
                # hop le (header "MZ") - phong truong hop tai nham trang
                # loi (vd HTML) duoi long .exe.
                if not os.path.exists(installer_path):
                    raise Exception("File khong ton tai sau khi tai")
                file_size = os.path.getsize(installer_path)
                if file_size < 1024 * 1024:  # Nho hon 1MB = chac chan loi
                    raise Exception(f"File qua nho ({file_size} bytes), co the bi loi")
                if total_size > 0 and file_size != total_size:
                    raise Exception(f"Kich thuoc file khong khop: {file_size} vs {total_size}")
                with open(installer_path, 'rb') as f:
                    header = f.read(2)
                if header != b'MZ':
                    raise Exception("File tai ve khong phai file thuc thi hop le")

                self._log(f"Da tai xong ({file_size // (1024 * 1024)}MB). Dang chuan bi cai dat...", 'info')

            except Exception as e:
                self._log(f"LOI tai ban cap nhat: {e}", 'err')
                self._js("uiApi.setStatus('Tai that bai. Thu lai sau.')")
                self._js("uiApi.setProgress(0, '')")
                for _ in range(3):
                    try:
                        if os.path.exists(installer_path):
                            os.remove(installer_path)
                        break
                    except Exception:
                        time.sleep(0.5)
                self._update_in_progress = False
                return

            # ── Buoc 2: viet + khoi chay watcher .bat, watcher moi la noi
            # thuc su goi bo cai (bang "start /wait") ──
            # Ly do dung watcher rieng thay vi Python goi thang bo cai roi
            # cho (wait) tai cho: bo cai chay voi /FORCECLOSEAPPLICATIONS
            # co the buoc dong chinh tien trinh RENUP nay bat cu luc nao no
            # con giu khoa file - neu Python dang wait() trong luc bi dong
            # nhu vay, wait() se khong bao gio tra ve va ta mat luon kha
            # nang doc ma loi that su cua bo cai. Watcher (mot tien trinh
            # cmd doc lap) khong bi anh huong boi viec RENUP.exe bi dong,
            # nen no van doc duoc ma loi va bao cho nguoi dung neu that bai.
            try:
                bat_path = os.path.join(update_dir, f"_update_watch_{uuid.uuid4().hex}.bat")
                fail_msg = (
                    f"RENUP cap nhat that bai (ma loi %RC%). "
                    f"Vui long tai va cai lai thu cong tai: "
                    f"https://github.com/{GITHUB_REPO}/releases/latest"
                )
                lines = [
                    "@echo off",
                    (
                        f'start "" /wait "{installer_path}" '
                        "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART "
                        "/FORCECLOSEAPPLICATIONS /RESTARTAPPLICATIONS"
                    ),
                    'set "RC=%ERRORLEVEL%"',
                    (
                        'if not "%RC%"=="0" powershell -NoProfile -WindowStyle Hidden -Command '
                        '"Add-Type -AssemblyName System.Windows.Forms; '
                        f"[System.Windows.Forms.MessageBox]::Show('{fail_msg}',"
                        "'RENUP - Loi cap nhat',[System.Windows.Forms.MessageBoxButtons]::OK,"
                        '[System.Windows.Forms.MessageBoxIcon]::Error)"'
                    ),
                    f'del "{installer_path}" >nul 2>&1',
                    'del "%~f0" >nul 2>&1',
                    '',
                ]
                with open(bat_path, 'w', encoding='utf-8') as f:
                    f.write("\r\n".join(lines))

                subprocess.Popen(
                    ['cmd', '/c', bat_path],
                    creationflags=subprocess.CREATE_NO_WINDOW
                )
            except Exception as e:
                self._log(f"LOI khoi chay bo cai dat: {e}", 'err')
                self._js("uiApi.setStatus('Khong the khoi chay bo cai. Thu lai sau.')")
                self._js("uiApi.setProgress(0, '')")
                for p in (installer_path, bat_path):
                    if not p:
                        continue
                    try:
                        if os.path.exists(p):
                            os.remove(p)
                    except Exception:
                        pass
                self._update_in_progress = False
                return

            # ── Buoc 3: watcher da chay bo cai o nen - gio tu dong (kill
            # ffmpeg dang chay + dong cua so) de giai phong khoa file cho
            # bo cai ghi de. Chu dong tu dong TRUOC thay vi dua hoan toan
            # vao /FORCECLOSEAPPLICATIONS - dong sach se hon (kill dung
            # cach cac tien trinh con ffmpeg/yt-dlp) va nhanh hon la de bo
            # cai tu buoc dong ep. ──
            self._log("=== Da khoi chay bo cai dat. RENUP se tu dong dong de cap nhat... ===", 'ok')
            self._js("uiApi.setStatus('Dang cai dat, RENUP se tu khoi dong lai...')")
            self._js("uiApi.setProgress(100, 'Dang cai dat...')")

            time.sleep(0.6)
            self._teardown_for_restart()

        threading.Thread(target=_dl, daemon=True).start()

    def _ver_cmp(self, v1, v2):
        def parse(v):
            v = v.lstrip('v').split('-')[0].split('+')[0]
            return [int(x) for x in v.split('.') if x.isdigit()]
        try:
            p1, p2 = parse(v1), parse(v2)
            for a, b in zip(p1, p2):
                if a != b: return a - b
            return len(p1) - len(p2)
        except Exception:
            return 0


# ══════════════════════════════════════════════════════════════

def main():
    api = Api()

    # Find HTML
    ui_dir = os.path.join(get_bundle_dir(), 'ui')
    if not os.path.exists(ui_dir):
        ui_dir = os.path.join(get_app_dir(), 'ui')
    html_path = os.path.join(ui_dir, 'index.html')

    icon_path = os.path.join(get_bundle_dir(), 'icon.png')
    if not os.path.exists(icon_path):
        icon_path = os.path.join(get_app_dir(), 'icon.png')
    if not os.path.exists(icon_path):
        icon_path = None

    window = webview.create_window(
        'RENUP',
        html_path,
        js_api=api,
        width=1200, height=750,
        min_size=(950, 600),
    )
    api.set_window(window)
    window.events.loaded += api.init
    window.events.closing += api.cleanup

    webview.start(debug=False)


if __name__ == '__main__':
    main()
