import sys
import os
import subprocess
import threading
import json
import urllib.request
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
        self._lock = threading.Lock()
        self._window = None
        self._code_map = {}

    def set_window(self, window):
        self._window = window

    def _js(self, code):
        if self._window:
            self._window.evaluate_js(code)

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
        ok_count = [0]
        done_count = [0]

        def update(success):
            with self._lock:
                if success: ok_count[0] += 1
                done_count[0] += 1
                d = done_count[0]
            pct = int(d / total * 100)
            self._js(f"uiApi.setProgress({pct}, '{d}/{total}')")
            self._js(f"uiApi.setStatus('Dang convert... {d}/{total} file')")

        def convert_one(i, mp4):
            mp3 = os.path.splitext(mp4)[0] + '.mp3'
            inp = os.path.join(input_dir, mp4)
            out = os.path.join(output_dir, mp3)
            self._log(f"[{i}/{total}] {mp4} -> {mp3}", 'info')
            dur = self._get_duration(inp)
            cmd = [self.ffmpeg_path, '-i', inp, '-vn', '-acodec', 'libmp3lame', '-q:a', '2',
                   '-progress', 'pipe:1', '-nostats', out, '-y']
            return self._run_ffmpeg(cmd, i, total, dur, mp3)

        futures = {}
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for i, mp4 in enumerate(mp4s, 1):
                futures[ex.submit(convert_one, i, mp4)] = i
            for f in as_completed(futures):
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
                                     text=True, creationflags=subprocess.CREATE_NO_WINDOW)
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

    def _run_reencode(self, params, code):
        self._js("uiApi.setStatus('Dang nen video...')")
        self._js("uiApi.setProgress(0, '')")
        self._log(f"=== Bat dau: {code.get('name', 'Re-encode')} ===", 'info')

        input_dir = params['inputDir']
        output_dir = params['outputDir']
        workers = max(1, params.get('workers', 2))
        cmd_template = code.get('command', '')

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
        self._log(f"Tim thay {total} video | {workers} luong.", 'info')
        ok_count = [0]
        done_count = [0]

        def update(success):
            with self._lock:
                if success: ok_count[0] += 1
                done_count[0] += 1
                d = done_count[0]
            self._js(f"uiApi.setProgress({int(d/total*100)}, '{d}/{total}')")
            self._js(f"uiApi.setStatus('Dang nen... {d}/{total} video')")

        def encode_one(i, vf):
            inp = os.path.join(input_dir, vf)
            out = os.path.join(output_dir, vf)
            self._log(f"[{i}/{total}] Nen: {vf}", 'info')
            dur = self._get_duration(inp)

            raw = cmd_template.replace('{input}', inp).replace('{output}', out)
            parts = raw.split()
            if parts and parts[0].lower() in ('ffmpeg', 'ffmpeg.exe'): parts = parts[1:]
            parts = [p for p in parts if p != '-y']
            cmd = [self.ffmpeg_path] + parts + ['-progress', 'pipe:1', '-nostats', '-y']

            return self._run_ffmpeg(cmd, i, total, dur, vf)

        futures = {}
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for i, vf in enumerate(files, 1):
                futures[ex.submit(encode_one, i, vf)] = i
            for f in as_completed(futures):
                try: success, _ = f.result()
                except: success = False
                update(success)

        self._log(f"=== Hoan thanh: {ok_count[0]}/{total} video ===", 'ok')
        self._js(f"uiApi.setStatus('Xong! {ok_count[0]}/{total} video.')")

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
        ok_count = [0]
        done_count = [0]

        def update(success):
            with self._lock:
                if success: ok_count[0] += 1
                done_count[0] += 1
                d = done_count[0]
            self._js(f"uiApi.setProgress({int(d/total*100)}, '{d}/{total}')")
            self._js(f"uiApi.setStatus('Dang convert... {d}/{total} anh')")

        def convert_one(i, filename):
            inp = os.path.join(input_dir, filename)
            new_name = os.path.splitext(filename)[0] + to_ext
            out = os.path.join(output_dir, new_name)
            self._log(f"[{i}/{total}] {filename} -> {new_name}", 'info')
            try:
                img = Image.open(inp)
                # Convert RGBA to RGB for JPG (no alpha support)
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
                self._log(f"  [{i}/{total}] OK: {new_name}", 'ok')
                return (True, new_name)
            except Exception as e:
                self._log(f"  [{i}/{total}] LOI: {e}", 'err')
                return (False, new_name)

        futures = {}
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for i, f in enumerate(files, 1):
                futures[ex.submit(convert_one, i, f)] = i
            for f in as_completed(futures):
                try:
                    success, _ = f.result()
                except:
                    success = False
                update(success)

        self._log(f"=== Hoan thanh: {ok_count[0]}/{total} anh ===", 'ok')
        self._js(f"uiApi.setStatus('Xong! {ok_count[0]}/{total} anh.')")

    # ── FFmpeg runner ──

    def _run_ffmpeg(self, cmd, i, total, duration, label):
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 text=True, creationflags=subprocess.CREATE_NO_WINDOW)
        stderr_lines = []

        def drain():
            for line in proc.stderr:
                stderr_lines.append(line)
        t = threading.Thread(target=drain, daemon=True)
        t.start()

        last_pct = -1
        for line in proc.stdout:
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

        if proc.returncode == 0:
            self._log(f"  [{i}/{total}] OK: {label}", 'ok')
            return True, label

        err = ''.join(stderr_lines).strip()
        last_err = err.splitlines()[-1] if err else 'Unknown error'
        self._log(f"  [{i}/{total}] LOI: {last_err}", 'err')
        return False, label

    # ── Helpers ──

    def _get_duration(self, filepath):
        try:
            r = subprocess.run(
                [self.ffprobe_path, '-v', 'quiet', '-print_format', 'json', '-show_format', filepath],
                capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
            return float(json.loads(r.stdout)['format']['duration'])
        except Exception:
            return 0.0

    # ── Auto Update ──

    def _check_update(self):
        try:
            req = urllib.request.Request(GITHUB_API, headers={"User-Agent": "RENUP-Updater"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
            latest = data.get("tag_name", "").lstrip("v")
            if not latest:
                return
            current = get_version()
            if self._ver_cmp(latest, current) > 0:
                download_url = ""
                for asset in data.get("assets", []):
                    if asset["name"].lower().endswith(".exe"):
                        download_url = asset["browser_download_url"]
                        break
                body = data.get("body", "").replace("'", "\\'").replace("\n", "\\n")[:200]
                self._js(f"""
                    if(confirm('Co ban cap nhat moi v{latest}!\\n\\nPhien ban hien tai: v{current}\\nPhien ban moi: v{latest}\\n\\n{body}\\n\\nBam OK de cap nhat.')){{
                        pywebview.api.downloadUpdate('{download_url}');
                    }}
                """)
        except Exception:
            pass

    def downloadUpdate(self, url):
        if not url:
            webbrowser.open(f"https://github.com/{GITHUB_REPO}/releases/latest")
            return
        self._log("=== Dang tai ban cap nhat... ===", 'info')
        self._js("uiApi.setStatus('Dang tai ban cap nhat...')")

        def _dl():
            try:
                app_dir = get_app_dir()
                new_exe = os.path.join(app_dir, "RENUP.exe.new")
                old_exe = os.path.join(app_dir, "RENUP.exe.old")
                cur_exe = os.path.join(app_dir, "RENUP.exe")

                req = urllib.request.Request(url, headers={"User-Agent": "RENUP-Updater"})
                with urllib.request.urlopen(req, timeout=120) as resp:
                    with open(new_exe, 'wb') as f:
                        shutil.copyfileobj(resp, f)

                if os.path.exists(cur_exe):
                    if os.path.exists(old_exe): os.remove(old_exe)
                    os.rename(cur_exe, old_exe)
                os.rename(new_exe, cur_exe)

                self._log("=== Cap nhat thanh cong! Khoi dong lai. ===", 'ok')
                self._js("uiApi.setStatus('Cap nhat xong! Hay khoi dong lai.')")
                self._js("alert('Cap nhat thanh cong!\\nHay dong va mo lai RENUP.')")
            except Exception as e:
                self._log(f"LOI cap nhat: {e}", 'err')
                if os.path.exists(new_exe): os.remove(new_exe)

        threading.Thread(target=_dl, daemon=True).start()

    def _ver_cmp(self, v1, v2):
        def parse(v): return [int(x) for x in v.split('.')]
        try:
            p1, p2 = parse(v1), parse(v2)
            for a, b in zip(p1, p2):
                if a != b: return a - b
            return len(p1) - len(p2)
        except: return 0


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

    webview.start(debug=False)


if __name__ == '__main__':
    main()
