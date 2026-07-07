import sys
import os
import re
import subprocess
import threading
import json
import random
import urllib.request
import urllib.error
import webbrowser
import uuid
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
    def __init__(self):
        app_dir = get_app_dir()
        self.bin_dir = os.path.join(app_dir, 'bin')
        self.codes_dir = os.path.join(self.bin_dir, 'codes')
        self.ffmpeg_path = os.path.join(self.bin_dir, 'ffmpeg.exe')
        self.ffprobe_path = os.path.join(self.bin_dir, 'ffprobe.exe')
        self.noi_txt_path = os.path.join(self.bin_dir, 'Noi.txt')
        self.claim_state_path = os.path.join(self.bin_dir, 'claim_state.json')
        self.ytdlp_path = os.path.join(self.bin_dir, 'yt-dlp.exe')
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
        """Kill all tracked FFmpeg processes."""
        for proc in self._current_procs:
            try:
                self._resume_process(proc.pid)
                proc.kill()
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
        status = 'done' if success else 'error'
        self._js(f"uiApi.updateProcessItem({idx}, 100, '{status}')")
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
        self._js(f"uiApi.showConvertSection({str(code_type == 'convert_video').lower()})")
        self._js(f"uiApi.showOverlaySection({str(code_type == 'overlay_corner').lower()})")
        self._js(f"uiApi.showMultiFolderSection({str(code_type == 'concat_multi_folder').lower()})")
        self._js(f"uiApi.showClaimSection({str(code_type == 'claim_tiktok').lower()})")

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
                if code_type == 'concat':
                    self._run_concat(params)
                elif code_type == 'convert_mp3':
                    self._run_convert(params)
                elif code_type == 'split_video':
                    self._run_split(params)
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
                else:
                    self._log(f"Khong ho tro type: {code_type}", 'err')
            except Exception as e:
                self._log(f"LOI: {e}", 'err')
            finally:
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

        total = len(mp4s)
        self._log(f"Tim thay {total} file | {workers} luong.", 'info')
        self._js(f"uiApi.initProcessTable({json.dumps(mp4s)})")

        ok_count = [0]
        done_count = [0]

        def update(idx, success):
            with self._lock:
                if success: ok_count[0] += 1
                done_count[0] += 1
                d = done_count[0]
            status = 'done' if success else 'error'
            self._js(f"uiApi.updateProcessItem({idx}, 100, '{status}')")
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
            for i, mp4 in enumerate(mp4s):
                futures[ex.submit(convert_one, i, mp4)] = i
            for f in as_completed(futures):
                idx = futures[f]
                try: success, _ = f.result()
                except: success = False
                update(success)

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

        total = len(files)
        self._total_tasks = total
        self._task_results = {}
        self._log(f"Tim thay {total} video | {workers} luong.", 'info')

        files_json = json.dumps(files)
        self._js(f"uiApi.initProcessTable({files_json})")

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

        all_tasks = [(i, make_task(i, vf)) for i, vf in enumerate(files)]
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

        total = len(files)
        self._log(f"Tim thay {total} anh | {workers} luong.", 'info')
        self._js(f"uiApi.initProcessTable({json.dumps(files)})")

        ok_count = [0]
        done_count = [0]

        def update(idx, success):
            with self._lock:
                if success: ok_count[0] += 1
                done_count[0] += 1
                d = done_count[0]
            status = 'done' if success else 'error'
            self._js(f"uiApi.updateProcessItem({idx}, 100, '{status}')")
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
            for i, f in enumerate(files):
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

        total = len(files)
        self._log(f"Tim thay {total} anh | overlay: {os.path.basename(overlay_path)} | {workers} luong.", 'info')
        self._js(f"uiApi.initProcessTable({json.dumps(files)})")

        ok_count = [0]
        done_count = [0]

        def update(idx, success):
            with self._lock:
                if success: ok_count[0] += 1
                done_count[0] += 1
                d = done_count[0]
            status = 'done' if success else 'error'
            self._js(f"uiApi.updateProcessItem({idx}, 100, '{status}')")
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
            for i, f in enumerate(files):
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

        total = len(goc_list)
        self._log(
            f"Tim thay {total} video goc | "
            f"Kich Ban: {len(kichban_list)} | Art: {len(art_list)} | Edit: {len(edit_list)} | "
            f"{workers} luong.",
            'info'
        )
        self._js(f"uiApi.initProcessTable({json.dumps(goc_list)})")

        ok_count = [0]
        done_count = [0]

        def update(idx, success):
            with self._lock:
                if success: ok_count[0] += 1
                done_count[0] += 1
                d = done_count[0]
            status = 'done' if success else 'error'
            self._js(f"uiApi.updateProcessItem({idx}, 100, '{status}')")
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
            for i, goc_filename in enumerate(goc_list):
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

        total = len(files)
        self._log(f"Tim thay {total} file | {workers} luong.", 'info')
        self._js(f"uiApi.initProcessTable({json.dumps(files)})")

        ok_count = [0]
        done_count = [0]

        def update(idx, success):
            with self._lock:
                if success: ok_count[0] += 1
                done_count[0] += 1
                d = done_count[0]
            status = 'done' if success else 'error'
            self._js(f"uiApi.updateProcessItem({idx}, 100, '{status}')")
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
            for i, f in enumerate(files):
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

        total = len(files)
        self._log(f"Tim thay {total} file | {workers} luong.", 'info')
        self._js(f"uiApi.initProcessTable({json.dumps(files)})")

        ok_count = [0]
        done_count = [0]

        def update(idx, success):
            with self._lock:
                if success: ok_count[0] += 1
                done_count[0] += 1
                d = done_count[0]
            status = 'done' if success else 'error'
            self._js(f"uiApi.updateProcessItem({idx}, 100, '{status}')")
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
            for i, f in enumerate(files):
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

    CONVERT_TARGETS = {
        'MP4':  {'ext': '.mp4',  'args': ['-c:v', 'libx264', '-preset', 'medium', '-crf', '20', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart']},
        'MOV':  {'ext': '.mov',  'args': ['-c:v', 'libx264', '-preset', 'medium', '-crf', '20', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '192k']},
        'MKV':  {'ext': '.mkv',  'args': ['-c:v', 'libx264', '-preset', 'medium', '-crf', '20', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '192k']},
        'WEBM': {'ext': '.webm', 'args': ['-c:v', 'libvpx-vp9', '-crf', '30', '-b:v', '0', '-deadline', 'good', '-cpu-used', '4', '-c:a', 'libopus', '-b:a', '128k']},
        'AVI':  {'ext': '.avi',  'args': ['-c:v', 'mpeg4', '-vtag', 'XVID', '-q:v', '5', '-c:a', 'libmp3lame', '-q:a', '4']},
        'FLV':  {'ext': '.flv',  'args': ['-c:v', 'libx264', '-preset', 'medium', '-crf', '23', '-c:a', 'aac', '-b:a', '128k', '-ar', '44100']},
        'WMV':  {'ext': '.wmv',  'args': ['-c:v', 'wmv2', '-b:v', '4M', '-c:a', 'wmav2', '-b:a', '192k']},
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

        # Source = all supported video exts EXCEPT target ext (avoid clobbering input)
        from_exts = [e for e in self.CONVERT_SOURCE_EXTS if e != to_ext]
        files = sorted(
            f for f in os.listdir(input_dir)
            if os.path.splitext(f)[1].lower() in from_exts
        )
        if not files:
            self._log(f"Khong tim thay video nguon (bo qua *{to_ext}).", 'err')
            return

        total = len(files)
        self._log(f"Tim thay {total} video | {workers} luong | -> {target}.", 'info')
        self._js(f"uiApi.initProcessTable({json.dumps(files)})")

        ok_count = [0]
        done_count = [0]

        def update(idx, success):
            with self._lock:
                if success: ok_count[0] += 1
                done_count[0] += 1
                d = done_count[0]
            status = 'done' if success else 'error'
            self._js(f"uiApi.updateProcessItem({idx}, 100, '{status}')")
            self._js(f"uiApi.setProgress({int(d/total*100)}, '{d}/{total}')")
            self._js(f"uiApi.setStatus('Dang convert sang {target}... {d}/{total}')")

        def convert_one(idx, filename):
            self._js(f"uiApi.updateProcessItem({idx}, 0, 'running')")
            inp = os.path.join(input_dir, filename)
            new_name = os.path.splitext(filename)[0] + to_ext
            out = os.path.join(output_dir, new_name)
            dur = self._get_duration(inp)
            parts = ['-i', inp] + spec['args'] + [out]
            if use_gpu:
                parts = self._swap_to_gpu(parts)
            cmd = [self.ffmpeg_path] + parts + ['-progress', 'pipe:1', '-nostats', '-y']
            success, _ = self._run_ffmpeg_with_table(cmd, idx, dur, new_name)
            return success

        futures = {}
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for i, f in enumerate(files):
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

        total = len(files)
        self._log(f"Tim thay {total} video | {workers} luong | target: {target}s (~{target/3600:.1f}h).", 'info')
        self._js(f"uiApi.initProcessTable({json.dumps(files)})")

        ok_count = [0]
        done_count = [0]

        def update(idx, success):
            with self._lock:
                if success: ok_count[0] += 1
                done_count[0] += 1
                d = done_count[0]
            status = 'done' if success else 'error'
            self._js(f"uiApi.updateProcessItem({idx}, 100, '{status}')")
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
            for i, f in enumerate(files):
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
        rows = []
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
            rows.append({'final_name': fn, 'music_name': mn, 'voice_id': vi, 'music_url': mu})
        return rows

    def _safe_filename(self, name):
        import re
        return re.sub(r'[\\/:*?"<>|]', '_', name)


    def _download_voice_tiktok(self, voice_id, voice_dir, device_id):
        if self._stopped:
            return False
        voice_path = os.path.join(voice_dir, f"{voice_id}.mp3")
        url = f"https://www.tiktok.com/music/original-sound-{voice_id}"
        cmd = [
            self.ytdlp_path,
            "--extractor-args", f"tiktok:device_id={device_id}",
            "--playlist-end", "1",
            "-x", "--audio-format", "mp3",
            "--ffmpeg-location", self.ffmpeg_path,
            "-o", os.path.join(voice_dir, f"{voice_id}.%(ext)s"),
            "--no-playlist",
            url,
        ]
        stderr_lines = []
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                creationflags=subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS,
            )
            self._current_procs.append(proc)
            try:
                def drain():
                    for line in proc.stderr:
                        stderr_lines.append(line)
                t = threading.Thread(target=drain, daemon=True)
                t.start()
                proc.stdout.read()
                proc.wait()
                t.join()
            finally:
                if proc in self._current_procs:
                    self._current_procs.remove(proc)
        except Exception as e:
            self._log(f"Loi spawn yt-dlp: {e}", 'err')
            return False

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

    def _download_music_drive(self, url, dest_path):
        """Download a public Google Drive file directly over HTTP (no gdown).

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

            with open(part_path, 'wb') as f:
                while True:
                    if self._stopped:
                        return False
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)

            if not os.path.exists(part_path) or os.path.getsize(part_path) == 0:
                return False

            os.replace(part_path, dest_path)
            return True
        except Exception as e:
            self._log(f"Loi tai nhac Drive: {e}", 'err')
            return False
        finally:
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

        total = len(rows)
        labels = [r['final_name'] for r in rows]
        self._js(f"uiApi.initProcessTable({json.dumps(labels)})")

        ok_count = [0]
        done_count = [0]

        def update(idx, success):
            with self._lock:
                if success:
                    ok_count[0] += 1
                done_count[0] += 1
                d = done_count[0]
            status = 'done' if success else 'error'
            if self._stopped:
                status = 'stopped'
            self._js(f"uiApi.updateProcessItem({idx}, 100, '{status}')")
            self._js(f"uiApi.setProgress({int(d / total * 100)}, '{d}/{total}')")
            self._js(f"uiApi.setStatus('Dang xu ly... {d}/{total} dong')")

        def process_one(idx, row):
            self._js(f"uiApi.updateProcessItem({idx}, 0, 'running')")
            final_name = row['final_name']
            music_name = row['music_name']
            voice_id = row['voice_id']
            music_url = row['music_url']
            try:
                if self._stopped:
                    return False

                voice_path = os.path.join(voice_dir, f"{voice_id}.mp3")
                if not os.path.exists(voice_path):
                    ok = self._download_voice_tiktok(voice_id, voice_dir, device_id)
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
                    ok = self._download_music_drive(music_url, music_path)
                    if not ok or not os.path.exists(music_path):
                        self._log(f"[{idx + 1}] Tai nhac fail: {final_name}", 'err')
                        return False
                self._js(f"uiApi.updateProcessItem({idx}, 66, 'running')")

                out_path = os.path.join(output_dir, self._safe_filename(final_name) + '.wav')
                ok = self._concat_voice_music(voice_path, music_path, out_path, sample_rate, channels, idx, max_seconds)
                return ok

            except Exception as e:
                self._log(f"[{idx + 1}] LOI: {e}", 'err')
                return False

        self._log(f"Tim thay {total} dong | {workers} luong.", 'info')

        futures = {}
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for i, row in enumerate(rows):
                if self._stopped:
                    break
                futures[ex.submit(process_one, i, row)] = i
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
                    if asset["name"].lower().endswith(".exe"):
                        download_url = asset["browser_download_url"]
                        break
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
        if not url:
            webbrowser.open(f"https://github.com/{GITHUB_REPO}/releases/latest")
            return
        self._log("=== Dang tai ban cap nhat... ===", 'info')
        self._js("uiApi.setStatus('Dang tai ban cap nhat...')")
        self._js("uiApi.setProgress(0, 'Dang tai...')")

        def _dl():
            import time
            app_dir = get_app_dir()
            new_exe = os.path.join(app_dir, "RENUP_new.exe")
            try:
                # Download with progress
                req = urllib.request.Request(url, headers={"User-Agent": "RENUP-Updater"})
                with urllib.request.urlopen(req, timeout=600) as resp:
                    total_size = int(resp.headers.get('Content-Length', 0))
                    downloaded = 0
                    with open(new_exe, 'wb') as f:
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

                # Verify downloaded file
                if not os.path.exists(new_exe):
                    raise Exception("File khong ton tai sau khi tai")
                file_size = os.path.getsize(new_exe)
                if file_size < 1024 * 1024:  # Less than 1MB = corrupted
                    raise Exception(f"File qua nho ({file_size} bytes), co the bi loi")
                if total_size > 0 and file_size != total_size:
                    raise Exception(f"Kich thuoc file khong khop: {file_size} vs {total_size}")

                self._log(f"Da tai xong ({file_size // (1024*1024)}MB). Dang cap nhat...", 'info')

                # Create update batch script
                bat_path = os.path.join(app_dir, "_update.bat")
                cur_exe = os.path.join(app_dir, "RENUP.exe")
                new_name = os.path.basename(new_exe)
                with open(bat_path, 'w') as f:
                    f.write(f"""@echo off
cd /d "{app_dir}"
timeout /t 3 /nobreak >nul
REM Try to kill and replace up to 10 times
for /l %%a in (1,1,10) do (
    taskkill /F /IM "RENUP.exe" >nul 2>&1
    timeout /t 1 /nobreak >nul
    del "RENUP.exe" >nul 2>&1
    if not exist "RENUP.exe" goto do_rename
)
echo [LOI] Khong the xoa file cu. Thu lai sau.
del "{new_name}" >nul 2>&1
pause
exit /b 1

:do_rename
rename "{new_name}" "RENUP.exe"
if not exist "RENUP.exe" (
    echo [LOI] Cap nhat that bai.
    pause
    exit /b 1
)
REM Cleanup old files
del "RENUP.exe.old" >nul 2>&1
del ".update_cache" >nul 2>&1
start "" "RENUP.exe"
del "%~f0"
""")

                self._log("=== Cap nhat thanh cong! Dang khoi dong lai... ===", 'ok')
                self._js("uiApi.setStatus('Cap nhat xong! Dang khoi dong lai...')")
                self._js("uiApi.setProgress(100, 'Hoan tat')")

                time.sleep(1)
                subprocess.Popen(
                    ['cmd', '/c', bat_path],
                    creationflags=subprocess.CREATE_NO_WINDOW
                )
                # Close app
                if self._window:
                    self._window.destroy()

            except Exception as e:
                self._log(f"LOI cap nhat: {e}", 'err')
                self._js("uiApi.setStatus('Cap nhat that bai. Thu lai sau.')")
                self._js("uiApi.setProgress(0, '')")
                # Cleanup
                for _ in range(3):
                    try:
                        if os.path.exists(new_exe):
                            os.remove(new_exe)
                        break
                    except Exception:
                        import time
                        time.sleep(0.5)

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
