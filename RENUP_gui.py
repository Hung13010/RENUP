import sys
import os
import subprocess
import threading
import json
import urllib.request
import urllib.error
import webbrowser
import shutil
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
        # Trigger UI update for the initially selected function
        self._js("onFuncChanged()")
        self._load_noi_txt()
        threading.Thread(target=self._check_update, daemon=True).start()

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

    # ── UI actions ──

    def onFuncChanged(self, func_name):
        code = self._code_map.get(func_name, {})
        code_type = code.get('type', '')
        self._js(f"uiApi.showGhepSection({str(code_type == 'concat').lower()})")
        self._js(f"uiApi.showSplitSection({str(code_type == 'split_video').lower()})")

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

        def convert_one(idx, filename):
            self._js(f"uiApi.updateProcessItem({idx}, 0, 'running')")
            inp = os.path.join(input_dir, filename)
            new_name = os.path.splitext(filename)[0] + to_ext
            out = os.path.join(output_dir, new_name)
            dur = self._get_duration(inp)
            cmd = [self.ffmpeg_path, '-i', inp, '-vn',
                   '-acodec', 'libmp3lame', '-q:a', '2',
                   '-progress', 'pipe:1', '-nostats', out, '-y']
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

    def _get_duration(self, filepath):
        try:
            r = subprocess.run(
                [self.ffprobe_path, '-v', 'quiet', '-print_format', 'json', '-show_format', filepath],
                capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS)
            return float(json.loads(r.stdout)['format']['duration'])
        except Exception:
            return 0.0

    # ── Auto Update ──

    def _check_update(self):
        try:
            # Cache: only check once per hour
            cache_file = os.path.join(get_app_dir(), '.update_cache')
            if os.path.exists(cache_file):
                import time as _t
                if _t.time() - os.path.getmtime(cache_file) < 3600:
                    return

            req = urllib.request.Request(GITHUB_API, headers={"User-Agent": "RENUP-Updater"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())

            # Update cache
            try:
                open(cache_file, 'w').close()
            except Exception:
                pass

            latest = data.get("tag_name", "").lstrip("v")
            if not latest:
                return
            current = get_version()
            self._log(f"Kiem tra update: hien tai v{current}, moi nhat v{latest}", 'info')
            if self._ver_cmp(latest, current) > 0:
                download_url = ""
                for asset in data.get("assets", []):
                    if asset["name"].lower().endswith(".exe"):
                        download_url = asset["browser_download_url"]
                        break
                safe_url = download_url.replace("'", "\\'")
                self._js(f"showUpdateDialog('{current}', '{latest}', '{safe_url}')")
        except urllib.error.HTTPError as e:
            if e.code == 403:
                self._log("GitHub API rate limit. Thu lai sau.", 'info')
            else:
                self._log(f"API error: {e.code}", 'err')
        except urllib.error.URLError:
            pass  # No internet, silent
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
