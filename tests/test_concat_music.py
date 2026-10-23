"""Tests cho cac ham THUAN cua 'Ghep nhac dai' (preset type concat_music).

Nap RENUP_gui.py theo duong dan (file khong phai package). Chi kiem
_concat_music_pick, _concat_music_tracklist, _gn_bed_factor,
_gn_parse_target, _fmt_hms_floor, _ffmpeg_err_line va nhan dien .txt cua
chinh RUN - khong can GUI, khong ffmpeg.
"""
import importlib.util
import json
import math
import os
import random

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(os.path.dirname(_HERE), 'RENUP_gui.py')
_spec = importlib.util.spec_from_file_location('renup_gui', _SRC)
renup_gui = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(renup_gui)
Api = renup_gui.Api

pick = Api._concat_music_pick
tracklist = Api._concat_music_tracklist


def _pool(durs):
    return [(f"C:/music/song{i}.mp3", float(d)) for i, d in enumerate(durs)]


# ── _concat_music_pick ──────────────────────────────────────────────

@pytest.mark.parametrize('seed', range(20))
def test_pick_reaches_target_overshoot_only_from_last_song(seed):
    pool = _pool([240, 180, 300, 200, 260, 150, 220, 310, 190, 280])
    target = 1500.0
    picked, total = pick(pool, target, False, random.Random(seed))
    assert total >= target
    assert total == pytest.approx(sum(d for _, d in picked))
    # Bo bai cuoi di thi chua toi dich: chi bai cuoi lam vuot.
    assert total - picked[-1][1] < target
    assert len({p for p, _ in picked}) == len(picked)        # khong lap


def test_pick_no_repeat_and_short_total_when_pool_too_small():
    pool = _pool([200, 210, 220])
    picked, total = pick(pool, 3600, False, random.Random(1))
    assert len(picked) == 3
    assert sorted(p for p, _ in picked) == sorted(p for p, _ in pool)
    assert total == pytest.approx(630.0)
    assert total < 3600


@pytest.mark.parametrize('seed', range(50))
def test_pick_loop_reaches_target_and_never_back_to_back(seed):
    pool = _pool([200, 210, 220, 230])
    target = 10800.0
    picked, total = pick(pool, target, True, random.Random(seed))
    assert total >= target
    assert total - picked[-1][1] < target
    paths = [p for p, _ in picked]
    assert len(paths) > len(pool)                              # da lap
    for a, b in zip(paths, paths[1:]):
        assert a != b
    # Moi vong (len(pool) bai) la mot hoan vi day du cua kho.
    n = len(pool)
    for start in range(0, len(paths) - n + 1, n):
        assert sorted(paths[start:start + n]) == sorted(p for p, _ in pool)


@pytest.mark.parametrize('seed', range(50))
def test_pick_loop_two_song_pool_alternates(seed):
    pool = _pool([100, 120])
    picked, total = pick(pool, 2000, True, random.Random(seed))
    paths = [p for p, _ in picked]
    assert total >= 2000
    for a, b in zip(paths, paths[1:]):
        assert a != b


def test_pick_single_song_pool_with_loop():
    pool = _pool([250])
    picked, total = pick(pool, 1000, True, random.Random(3))
    assert len(picked) == 4                                    # 4 * 250 = 1000
    assert total == pytest.approx(1000.0)
    assert {p for p, _ in picked} == {pool[0][0]}


def test_pick_single_song_pool_without_loop():
    pool = _pool([250])
    picked, total = pick(pool, 1000, False, random.Random(3))
    assert picked == pool
    assert total == pytest.approx(250.0)


def test_pick_empty_pool():
    assert pick([], 3600, False, random.Random(0)) == ([], 0.0)
    assert pick([], 3600, True, random.Random(0)) == ([], 0.0)


def test_pick_ignores_zero_duration_songs_and_terminates():
    pool = [("a.mp3", 0.0), ("b.mp3", 100.0), ("c.mp3", -5.0)]
    picked, total = pick(pool, 500, True, random.Random(0))
    assert {p for p, _ in picked} == {"b.mp3"}
    assert total == pytest.approx(500.0)
    assert pick([("a.mp3", 0.0)], 500, True, random.Random(0)) == ([], 0.0)


def test_pick_is_reproducible_with_same_rng_seed():
    pool = _pool([200, 210, 220, 230, 240])
    assert pick(pool, 3000, True, random.Random(7)) == pick(pool, 3000, True, random.Random(7))


@pytest.mark.parametrize('target', [math.inf, -math.inf, math.nan, float('1e400')])
@pytest.mark.parametrize('loop', [False, True])
def test_pick_non_finite_target_returns_empty(target, loop):
    # Che do lap + dich inf truoc day boc bai mai khong dung (Stop cung khong cat duoc).
    assert pick(_pool([200, 210]), target, loop, random.Random(0)) == ([], 0.0)


# ── _gn_parse_target (o Thoi luong dich) ────────────────────────────

CAP = Api.GN_MAX_TARGET_DEFAULT


@pytest.mark.parametrize('raw', ["", "   ", None, "abc", "0", "0:00:00", "-5", "inf",
                                 "Infinity", "-inf", "nan", "1e400", "1:nan:00", "1:60:00"])
def test_gn_parse_target_rejects_invalid(raw):
    t, why = Api._gn_parse_target(raw, CAP)
    assert t is None
    assert why.startswith("Thoi luong dich khong hop le:")


@pytest.mark.parametrize('raw, secs', [
    ("90", 90.0), ("1:30:00", 5400.0), ("3:00:00", 10800.0), (" 2:00 ", 120.0),
    ("10800", 10800.0), ("100:00:00", 360000.0),               # dung bang gioi han: duoc
])
def test_gn_parse_target_accepts_valid(raw, secs):
    assert Api._gn_parse_target(raw, CAP) == (pytest.approx(secs), None)


@pytest.mark.parametrize('raw', ["100:00:01", "360001", "3000:00:00", "1e300"])
def test_gn_parse_target_rejects_above_cap(raw):
    t, why = Api._gn_parse_target(raw, CAP)
    assert t is None
    assert "vuot gioi han 100:00:00" in why and "max_target_seconds" in why
    assert f"'{raw}'" in why                    # in lai dung chu nguoi dung go


def test_gn_parse_target_custom_cap():
    assert Api._gn_parse_target("1:00:00", 3600) == (3600.0, None)
    t, why = Api._gn_parse_target("1:00:01", 3600)
    assert t is None and "vuot gioi han 01:00:00" in why


@pytest.mark.parametrize('bad_cap', [None, "", "abc", 0, -5, math.inf, math.nan])
def test_gn_parse_target_bad_cap_falls_back_to_100h(bad_cap):
    assert Api._gn_parse_target("100:00:00", bad_cap) == (360000.0, None)
    assert Api._gn_parse_target("100:00:01", bad_cap)[0] is None


def test_preset_declares_max_target_seconds():
    path = os.path.join(os.path.dirname(_HERE), 'bin', 'codes', 'Ghep nhac Jazz', 'Ghep nhac dai.json')
    with open(path, encoding='utf-8') as fh:
        preset = json.load(fh)
    assert preset['type'] == 'concat_music'
    assert preset['max_target_seconds'] == Api.GN_MAX_TARGET_DEFAULT
    assert '_comment_max_target_seconds' in preset


# ── _concat_music_tracklist ─────────────────────────────────────────

def _rows(text):
    lines = text.split('\n')
    i = next(k for k, ln in enumerate(lines) if ln.startswith(' STT'))
    rows = []
    for ln in lines[i + 1:]:
        if not ln.strip():
            break
        rows.append(ln)
    return lines, rows


def test_tracklist_layout_hours_always_and_floor_starts():
    items = [("Song A.mp3", 252.4), ("Song B.mp3", 238.6), ("Song C.mp3", 3600.7)]
    txt = tracklist("Lofi 3h.mp3", items, 10800, '', r'D:\Music\Lofi',
                    output_dir=r'D:\Music\Out')
    lines, rows = _rows(txt)
    assert lines[0] == "GHEP NHAC DAI - DANH SACH BAI"
    assert lines[1] == "File         : Lofi 3h.mp3"
    # 252.4 + 238.6 + 3600.7 = 4091.7 -> floor 4091 -> 01:08:11
    assert lines[2] == "Thoi luong   : 01:08:11  (dich 03:00:00)"
    assert lines[3] == "So bai       : 3"                     # khong lap -> khong ghi
    assert lines[4] == "Random       : khong"
    assert lines[5] == "Am nen       : (khong)"
    assert lines[6] == "Am luong nen : (khong)"
    assert lines[7] == r"Input        : D:\Music\Lofi"
    assert lines[8] == r"Output       : D:\Music\Out"
    assert lines[9] == ""
    assert lines[10] == " STT  BAT DAU   TEN FILE"
    # Moc floor cua tong cong don: 0, 252.4 -> 252, 491.0 -> 491
    assert rows == [
        "   1  00:00:00  Song A.mp3",
        "   2  00:04:12  Song B.mp3",
        "   3  00:08:11  Song C.mp3",
    ]
    assert "BO QUA" not in txt
    assert txt.endswith("\n")


def test_tracklist_has_no_duration_column():
    items = [("A.mp3", 100.0), ("B.mp3", 100.0)]
    lines, rows = _rows(tracklist("o.mp3", items, 200, 'rain.mp3'))
    header = next(ln for ln in lines if ln.startswith(' STT'))
    assert "THOI LUONG" not in header
    assert header.split() == ["STT", "BAT", "DAU", "TEN", "FILE"]
    # Moi dong chi con STT, moc, ten file (ten co the co khoang trang).
    assert [r.split(None, 2) for r in rows] == [
        ["1", "00:00:00", "A.mp3"], ["2", "00:01:40", "B.mp3"]]


def test_tracklist_name_column_aligned_with_header():
    items = [("A.mp3", 100.0), ("Ten co dau cach.mp3", 100.0)]
    lines, rows = _rows(tracklist("o.mp3", items + [("x.mp3", 5.0)] * 10))
    col = next(ln for ln in lines if ln.startswith(' STT')).index("TEN FILE")
    assert rows[0][col:] == "A.mp3"
    assert rows[1][col:] == "Ten co dau cach.mp3"
    assert rows[11][col:] == "x.mp3"                         # STT 2 chu so


def _hms(s):
    h, m, x = s.split(':')
    return int(h) * 3600 + int(m) * 60 + int(x)


def _header_total(lines):
    return _hms(lines[2].split(':', 1)[1].split()[0])


def test_tracklist_floor_starts_small_example():
    items = [("a.mp3", 2.6), ("b.mp3", 2.6), ("c.mp3", 2.6)]
    lines, rows = _rows(tracklist("o.mp3", items))
    # cum 0, 2.6, 5.2, 7.8 -> floor 0, 2, 5, 7
    assert [r.split()[1] for r in rows] == ["00:00:00", "00:00:02", "00:00:05"]
    assert lines[2] == "Thoi luong   : 00:00:07"


@pytest.mark.parametrize('seed', range(30))
def test_tracklist_starts_are_floor_of_cumulative_property(seed):
    rng = random.Random(seed)
    items = [(f"s{i}.mp3", rng.uniform(60, 400)) for i in range(rng.randint(1, 60))]
    lines, rows = _rows(tracklist("o.mp3", items, 10800))
    starts = [_hms(r.split()[1]) for r in rows]
    cum = 0.0
    for (_, d), s in zip(items, starts):
        assert s == int(cum + 1e-6)        # floor: khong bao gio chi qua som
        cum += d
    assert _header_total(lines) == int(cum + 1e-6) <= cum + 1e-6
    assert starts == sorted(starts)


def test_tracklist_floor_is_robust_to_float_accumulation():
    # 0.1 cong 2520 lan bang so thuc ra 251.99999...; moc phai la 252 chu khong 251.
    items = [("x.mp3", 0.1)] * 2520 + [("last.mp3", 10.0)]
    _, rows = _rows(tracklist("o.mp3", items))
    assert rows[-1].split()[1] == "00:04:12"


def test_tracklist_unique_count_only_with_repeats():
    items = [("A.mp3", 100.0), ("B.mp3", 100.0), ("A.mp3", 100.0), ("C.mp3", 100.0)]
    txt = tracklist("o.mp3", items, 400)
    assert "So bai       : 4  (3 bai khac nhau)" in txt


def _loop_line(items):
    lines = tracklist("o.mp3", items, 400).split('\n')
    assert lines[3].startswith("So bai ")
    return lines[4]


def test_tracklist_loop_line_no_repeats():
    items = [("A.mp3", 100.0), ("B.mp3", 100.0), ("C.mp3", 100.0)]
    assert _loop_line(items) == "Random       : khong"
    assert _loop_line([]) == "Random       : khong"


def test_tracklist_loop_line_equal_counts():
    items = [("A.mp3", 100.0), ("B.mp3", 100.0), ("A.mp3", 100.0), ("B.mp3", 100.0)]
    assert _loop_line(items) == "Random       : co - 2 bai, moi bai phat 2 lan"


def test_tracklist_loop_line_unequal_counts():
    # 8 bai: 5 bai phat 3 lan, 3 bai phat 2 lan -> 21 dong (nhu lan chay cua Hung).
    names = [f"s{i}.mp3" for i in range(8)]
    items = [(nm, 200.0) for nm in names] * 2 + [(nm, 200.0) for nm in names[:5]]
    assert len(items) == 21
    assert _loop_line(items) == "Random       : co - 8 bai, moi bai phat 2-3 lan"


def test_tracklist_header_colons_aligned():
    items = [("A.mp3", 100.0), ("B.mp3", 100.0), ("A.mp3", 100.0)]
    txt = tracklist("o: x.mp3", items, 3600, 'rain.mp3', r'D:\in', output_dir=r'D:\out')
    lines, _ = _rows(txt)
    header = lines[1:lines.index("")]
    assert [ln.split(' :')[0].strip() for ln in header] == [
        "File", "Thoi luong", "So bai", "Random", "Am nen", "Am luong nen", "Input", "Output"]
    assert {ln.index(' : ') for ln in header} == {12}


def test_tracklist_has_no_created_or_export_time():
    txt = tracklist("o.mp3", [("A.mp3", 100.0)], 100, 'rain.mp3', r'D:\in', output_dir=r'D:\out')
    assert "Tao luc" not in txt
    assert "Xuat mat" not in txt
    assert "Kho nguon" not in txt                              # da doi thanh "Input"


def _bed_lines(bed_name, bed_volume=1.0):
    """(dong "Am nen", dong "Am luong nen")."""
    lines = tracklist("o.mp3", [("A.mp3", 60.0)], 60, bed_name,
                      bed_volume=bed_volume).split('\n')
    assert lines[5].startswith("Am nen ")
    assert lines[6].startswith("Am luong nen ")
    return lines[5], lines[6]


def test_tracklist_bed_line_100_percent_is_equal_mix():
    assert _bed_lines('rain.mp3') == ("Am nen       : rain.mp3 - lap suot file",
                                      "Am luong nen : 100%  (ngang bang nhac)")
    assert _bed_lines('rain.mp3', 1.0) == _bed_lines('rain.mp3')


@pytest.mark.parametrize('factor, pct', [(0.6, 60), (0.5, 50), (0.0, 0),
                                         (1.5, 150), (2.0, 200), (0.05, 5)])
def test_tracklist_bed_line_other_percent(factor, pct):
    assert _bed_lines('rain.mp3', factor) == ("Am nen       : rain.mp3 - lap suot file",
                                              f"Am luong nen : {pct}%")


def test_tracklist_bed_line_no_bed():
    no_bed = ("Am nen       : (khong)", "Am luong nen : (khong)")
    assert _bed_lines('') == no_bed
    assert _bed_lines('', 0.6) == no_bed                       # khong co nen -> bo qua %


# ── _gn_bed_factor (thanh truot gnBedVolume -> he so) ───────────────

@pytest.mark.parametrize('raw, factor', [
    ("100", 1.0), ("60", 0.6), ("0", 0.0), ("200", 2.0), (" 45 ", 0.45),
    ("62.5", 0.625), (60, 0.6),
    ("250", 2.0), ("-5", 0.0),                                 # kep 0..200
    ("", 1.0), ("abc", 1.0), (None, 1.0), ("nan", 1.0), ("inf", 1.0),
])
def test_gn_bed_factor(raw, factor):
    assert Api._gn_bed_factor(raw) == pytest.approx(factor)


def test_gn_bed_factor_round_trips_into_bed_line():
    assert _bed_lines('rain.mp3', Api._gn_bed_factor("60"))[1] == "Am luong nen : 60%"
    assert _bed_lines('rain.mp3', Api._gn_bed_factor("100"))[1] == \
        "Am luong nen : 100%  (ngang bang nhac)"
    assert _bed_lines('rain.mp3', Api._gn_bed_factor(""))[1] == \
        "Am luong nen : 100%  (ngang bang nhac)"


def test_tracklist_no_target_omits_dich():
    txt = tracklist("o.mp3", [("A.mp3", 61.0)], None)
    assert "Thoi luong   : 00:01:01\n" in txt


def test_tracklist_skipped_section():
    items = [("A.mp3", 60.0)]
    txt = tracklist("o.mp3", items, 60, skipped=[("bad.mp3", "Invalid data found when processing input")])
    tail = txt.rstrip('\n').split('\n')[-3:]
    assert tail == ["", "BO QUA (khong doc duoc):",
                    "  - bad.mp3: Invalid data found when processing input"]


def test_tracklist_hours_over_99():
    txt = tracklist("o.mp3", [("A.mp3", 100 * 3600.0), ("B.mp3", 5.0)])
    _, rows = _rows(txt)
    assert rows[1].split()[1] == "100:00:00"


def test_fmt_hms():
    assert Api._fmt_hms(0) == "00:00:00"
    assert Api._fmt_hms(59.6) == "00:01:00"
    assert Api._fmt_hms(3599) == "00:59:59"
    assert Api._fmt_hms(10800) == "03:00:00"


# ── _fmt_hms_floor: MOI con so tong (log, status, tieu de, .txt) ─────

def test_fmt_hms_floor_examples():
    assert Api._fmt_hms_floor(0) == "00:00:00"
    assert Api._fmt_hms_floor(59.6) == "00:00:59"
    assert Api._fmt_hms_floor(3749.9) == "01:02:29"          # QA: log 01:02:30 vs 01:02:29
    assert Api._fmt_hms_floor(866.5) == "00:14:26"
    assert Api._fmt_hms_floor(865.9) == "00:14:25"           # QA: status 00:14:26 vs txt 00:14:25
    assert Api._fmt_hms_floor(251.99999999) == "00:04:12"    # nhieu cong don so thuc


@pytest.mark.parametrize('seed', range(30))
def test_fmt_hms_floor_matches_tracklist_header(seed):
    rng = random.Random(seed)
    items = [(f"s{i}.mp3", rng.uniform(60, 400)) for i in range(rng.randint(1, 60))]
    lines, _ = _rows(tracklist("o.mp3", items, 10800))
    total = sum(d for _, d in items)              # cung thu tu cong nhu _run_concat_music
    assert lines[2].split(':', 1)[1].split()[0] == Api._fmt_hms_floor(total)


# ── _ffmpeg_err_line: ly do BO QUA trong .txt ───────────────────────

# Duoi stderr THAT cua ffmpeg 2018 di kem, voi mp3 hong o giua (QA R6a).
_FF_FILTER_FAIL = """\
[mp3float @ 0000027491f4c040] Header missing
Error while decoding stream #0:0: Invalid data found when processing input
[abuffer @ 0000027491f4c040] Value inf for parameter 'time_base' out of range [0 - 2.14748e+009]
    Last message repeated 3 times
[abuffer @ 0000027491f4c040] Error setting option time_base to value 1/0.
[graph_0_in_0_0 @ 0000027491afa500] Error applying options to the filter.
Error configuring filter graph
Conversion failed!
"""

_FF_BANNER = """\
ffmpeg version 4.0.2 Copyright (c) 2000-2018 the FFmpeg developers
  built with gcc 7.3.1 (GCC) 20180722
  libpostproc    55.  2.100 / 55.  2.100
"""


@pytest.mark.parametrize('stderr, want', [
    (_FF_FILTER_FAIL, "Error configuring filter graph"),
    (_FF_FILTER_FAIL.replace('\n', '\r\n'), "Error configuring filter graph"),
    (_FF_BANNER + "[mov,mp4,m4a,3gp,3g2,mj2 @ 0000028a6dca2b00] moov atom not found\n"
                  "trunc.m4a: Invalid data found when processing input\n",
     "trunc.m4a: Invalid data found when processing input"),
    (_FF_BANNER + "[mp3 @ 000002482d622b80] Failed to read frame size: Could not seek to 51023.\n"
                  "bad.mp3: Invalid argument\n",
     "bad.mp3: Invalid argument"),
    (_FF_BANNER + "    Stream #0:0: Video: png, rgb24(pc), 64x64\n"
                  "Stream map '0:a:0' matches no streams.\n"
                  "To ignore this, add a trailing '?' to the map.\n",
     "Stream map '0:a:0' matches no streams."),
    ("x.mp3: No such file or directory\n", "x.mp3: No such file or directory"),
    ("Decoder (codec none) not found for input stream #0:0\nConversion failed!\n",
     "Decoder (codec none) not found for input stream #0:0"),
    ("could not find codec parameters\n\nConversion failed!\n\n",
     "could not find codec parameters"),
])
def test_ffmpeg_err_line_prefers_real_error(stderr, want):
    assert Api._ffmpeg_err_line(stderr) == want


def test_ffmpeg_err_line_fallbacks():
    # Khong dong nao giong loi -> dong cuoi khong phai trailer.
    assert Api._ffmpeg_err_line("foo\nbar baz\nConversion failed!\n") == "bar baz"
    # Chi co trailer -> van tra no (con hon 'Unknown error').
    assert Api._ffmpeg_err_line("\nConversion failed!\n") == "Conversion failed!"
    assert Api._ffmpeg_err_line("") == "Unknown error"
    assert Api._ffmpeg_err_line(None) == "Unknown error"


# ── Nhan ra ban ghep cu (.txt cung ten do RUN ghi) ──────────────────

@pytest.mark.parametrize('line, own', [
    ("GHEP NHAC DAI - DANH SACH BAI", True),
    ("GHEP NHAC DAI - DANH SACH BAI\n", True),
    ("GHEP NHAC DAI - DANH SACH BAI\r\n", True),
    ("\ufeffGHEP NHAC DAI - DANH SACH BAI\n", True),           # BOM chua bi bo
    ("ghep nhac dai - danh sach bai", False),
    ("Loi bai hat: Em cua ngay hom qua", False),
    ("DANH SACH BAI", False),
    ("", False),
    (None, False),
])
def test_gn_is_own_tracklist(line, own):
    assert Api._gn_is_own_tracklist(line) is own


def test_gn_is_own_tracklist_recognises_real_tracklist_output():
    txt = tracklist("Mix.mp3", [("A.mp3", 60.0)], 60, 'rain.mp3', r'D:\in', output_dir=r'D:\out')
    assert Api._gn_is_own_tracklist(txt.split('\n')[0])


def test_gn_has_own_tracklist_reads_file_written_like_run(tmp_path):
    # Ghi dung cach _run_concat_music ghi: utf-8-sig, che do text.
    with open(tmp_path / "Mix 3h.txt", 'w', encoding='utf-8-sig') as fh:
        fh.write(tracklist("Mix 3h.mp3", [("A.mp3", 60.0)], 60))
    (tmp_path / "Mix 3h.mp3").write_bytes(b'')
    (tmp_path / "Song.txt").write_text("Loi bai hat\n", encoding='utf-8')
    (tmp_path / "Song.mp3").write_bytes(b'')
    (tmp_path / "Lone.mp3").write_bytes(b'')
    (tmp_path / "Bin.txt").write_bytes(b'\xff\xfe\x00garbage')
    assert Api._gn_has_own_tracklist(str(tmp_path), "Mix 3h.mp3") is True
    assert Api._gn_has_own_tracklist(str(tmp_path), "Song.mp3") is False
    assert Api._gn_has_own_tracklist(str(tmp_path), "Lone.mp3") is False      # khong co .txt
    assert Api._gn_has_own_tracklist(str(tmp_path), "Bin.wav") is False       # .txt rac


# ── Nap toan bo kho khi vua chon Kho nhac ngan (khong Random) ──────────

def test_gn_name_key_sorts_like_explorer():
    names = ["b.mp3", "Song 10.mp3", "A.mp3", "song 2.mp3", "Carefree.mp3"]
    assert sorted(names, key=Api._gn_name_key) == [
        "A.mp3", "b.mp3", "Carefree.mp3", "song 2.mp3", "Song 10.mp3"]


def _list_api(durs):
    """Api khong GUI: _js ghi lai, _get_duration tra theo ten file."""
    api = Api.__new__(Api)
    api.calls = []
    api._js = api.calls.append
    api._gn_dur_cache = {}
    api._stopped = False
    api.is_running = True
    api._get_duration = lambda p: durs[os.path.basename(p)]
    return api


def _payload(api):
    js = next(c for c in api.calls if c.startswith("gnSetList("))
    return json.loads(js[len("gnSetList("):-1])


def test_load_pool_lists_whole_folder_in_name_order_without_target(tmp_path):
    durs = {"Song 10.mp3": 60.0, "a.mp3": 30.0, "Song 2.mp3": 45.0}
    for name in durs:
        (tmp_path / name).write_bytes(b'')
    (tmp_path / "ghi chu.txt").write_text("x", encoding='utf-8')
    api = _list_api(durs)
    # O dich con trong: nap kho van chay, khong bao loi (Random moi bao).
    api._gn_random_work(str(tmp_path), '', False, {}, shuffle=False)
    p = _payload(api)
    assert [t["name"] for t in p["tracks"]] == ["a.mp3", "Song 2.mp3", "Song 10.mp3"]
    assert p["target"] == 0
    assert p["total"] == pytest.approx(135.0)
    assert not any("khong hop le" in c for c in api.calls)
    assert api.is_running is False


def test_load_pool_ignores_target_and_loop_when_folder_longer(tmp_path):
    durs = {f"{i:02d}.mp3": 100.0 for i in range(1, 6)}
    for name in durs:
        (tmp_path / name).write_bytes(b'')
    api = _list_api(durs)
    api._gn_random_work(str(tmp_path), '150', True, {}, shuffle=False)
    p = _payload(api)
    assert [t["name"] for t in p["tracks"]] == ["01.mp3", "02.mp3", "03.mp3", "04.mp3", "05.mp3"]
    assert p["target"] == 150
    assert any("dai hon dich" in c for c in api.calls)


def test_random_still_rejects_bad_target(tmp_path):
    (tmp_path / "a.mp3").write_bytes(b'')
    api = _list_api({"a.mp3": 30.0})
    api._gn_random_work(str(tmp_path), '', False, {}, shuffle=True)
    assert not any(c.startswith("gnSetList(") for c in api.calls)
    assert any("khong hop le" in c for c in api.calls)
