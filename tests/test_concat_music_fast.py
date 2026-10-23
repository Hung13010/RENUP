"""Tests cho cac ham THUAN cua duong nhanh 'Ghep nhac dai' (ADR-040): khoa
cache, quyet dinh chep/ma hoa lai, duyet khung MP3, moc bat dau khi noi khung,
chia khuc ma hoa song song. Khong GUI, khong ffmpeg.
"""
import importlib.util
import json
import math
import os

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(os.path.dirname(_HERE), 'RENUP_gui.py')
_spec = importlib.util.spec_from_file_location('renup_gui', _SRC)
renup_gui = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(renup_gui)
Api = renup_gui.Api

SPF = Api.GN_MP3_SPF
KBPS = Api._GN_MP3_KBPS


# ── _gn_cache_key ───────────────────────────────────────────────────

def _key(**kw):
    a = dict(path=r'C:\Music\song.mp3', size=1000, mtime_ns=123456789, sr=44100, ch=2, mp3_br='192k')
    a.update(kw)
    return Api._gn_cache_key(a['path'], a['size'], a['mtime_ns'], a['sr'], a['ch'], a['mp3_br'])


def test_cache_key_is_sha1_hex_and_stable():
    k = _key()
    assert len(k) == 40 and all(c in '0123456789abcdef' for c in k)
    assert _key() == k


@pytest.mark.parametrize('change', [
    {'path': r'C:\Music\other.mp3'}, {'size': 1001}, {'mtime_ns': 123456790},
    {'sr': 48000}, {'ch': 1}, {'mp3_br': '256k'},
])
def test_cache_key_changes_with_source_or_output_params(change):
    assert _key(**change) != _key()


@pytest.mark.skipif(os.name != 'nt', reason='normcase chi gop hoa/thuong tren Windows')
def test_cache_key_ignores_case_and_slashes_on_windows():
    assert _key(path=r'c:/music/SONG.MP3') == _key()


# ── _gn_cache_mode ──────────────────────────────────────────────────

@pytest.mark.parametrize('probe, mode', [
    ({'codec_name': 'mp3', 'sample_rate': '44100', 'channels': 2}, 'copy'),
    ({'codec_name': 'MP3', 'sample_rate': 44100, 'channels': '2'}, 'copy'),
    ({'codec_name': 'mp3', 'sample_rate': '48000', 'channels': 2}, 'encode'),
    ({'codec_name': 'mp3', 'sample_rate': '44100', 'channels': 1}, 'encode'),
    ({'codec_name': 'aac', 'sample_rate': '44100', 'channels': 2}, 'encode'),
    ({'codec_name': 'flac', 'sample_rate': '44100', 'channels': 2}, 'encode'),
    ({'codec_name': 'mp3', 'sample_rate': 'N/A', 'channels': 2}, 'encode'),
    ({'codec_name': 'mp3'}, 'encode'),
    ({}, 'encode'),
    (None, 'encode'),
    ('mp3', 'encode'),
])
def test_cache_mode_from_probe(probe, mode):
    assert Api._gn_cache_mode(probe, 44100, 2) == mode


def test_cache_mode_follows_preset_rate():
    p = {'codec_name': 'mp3', 'sample_rate': '48000', 'channels': 2}
    assert Api._gn_cache_mode(p, 48000, 2) == 'copy'


# ── _gn_mp3_scan / _gn_copy_usable ──────────────────────────────────

def _frame(kbps=192, sr=44100, pad=0, mode=1, crc=False, mdb=0):
    """Mot khung MPEG-1 Layer III gia (header + side info dau, phan con lai 0)."""
    bi = KBPS.index(kbps)
    si = Api._GN_MP3_RATES.index(sr)
    b1 = 0xFA if crc else 0xFB
    hdr = bytes([0xFF, b1, (bi << 4) | (si << 2) | (pad << 1), mode << 6])
    ln = 144000 * kbps // sr + pad
    body = bytearray(ln - 4)
    o = 2 if crc else 0
    body[o] = (mdb >> 1) & 0xFF
    body[o + 1] = (mdb & 1) << 7
    return hdr + bytes(body)


def test_scan_counts_frames_and_offsets():
    frames = [_frame(192, pad=0), _frame(192, pad=1), _frame(320), _frame(128, pad=1)]
    data = b''.join(frames)
    offs, problem, mdb = Api._gn_mp3_scan(data, 44100, 2)
    assert problem is None and mdb == 0
    assert len(offs) == len(frames) + 1
    assert offs[0] == 0 and offs[-1] == len(data)
    assert [offs[i + 1] - offs[i] for i in range(len(frames))] == [len(f) for f in frames]
    assert [len(f) for f in frames] == [626, 627, 1044, 418]


def test_scan_partial_tail_and_junk_tail():
    good = _frame() * 5
    offs, problem, _ = Api._gn_mp3_scan(good + _frame()[:300], 44100, 2)
    assert problem == 'partial' and len(offs) == 6 and offs[-1] == len(good)
    offs, problem, _ = Api._gn_mp3_scan(good + b'TAG' + b'x' * 125, 44100, 2)
    assert problem == 'junk' and offs[-1] == len(good)
    offs, problem, _ = Api._gn_mp3_scan(good + b'\xff\xfb', 44100, 2)   # < 4 byte con lai
    assert problem == 'partial' and offs[-1] == len(good)


def test_scan_rejects_other_rate_and_mono_mismatch():
    data = _frame() * 3 + _frame(sr=48000) + _frame() * 2
    offs, problem, _ = Api._gn_mp3_scan(data, 44100, 2)
    assert problem == 'format' and len(offs) - 1 == 3
    assert Api._gn_mp3_scan(_frame(mode=3) * 2, 44100, 2)[1] == 'format'
    assert Api._gn_mp3_scan(_frame(mode=3) * 2, 44100, 1)[1] is None
    assert Api._gn_mp3_scan(_frame(mode=1) * 2, 44100, 1)[1] == 'format'


def test_scan_reads_main_data_begin_with_and_without_crc():
    assert Api._gn_mp3_scan(_frame(mdb=0) + _frame(mdb=300), 44100, 2)[2] == 0
    assert Api._gn_mp3_scan(_frame(mdb=311) + _frame(), 44100, 2)[2] == 311
    assert Api._gn_mp3_scan(_frame(mdb=257, crc=True) + _frame(), 44100, 2)[2] == 257


def test_scan_empty_and_garbage():
    assert Api._gn_mp3_scan(b'', 44100, 2) == ([0], None, None)
    offs, problem, mdb = Api._gn_mp3_scan(b'ID3\x04' + b'\x00' * 100, 44100, 2)
    assert offs == [0] and problem == 'junk' and mdb is None


def _usable(data):
    offs, problem, mdb = Api._gn_mp3_scan(data, 44100, 2)
    return Api._gn_copy_usable(offs, problem, mdb, len(data))


def test_copy_usable_trims_short_tail_only():
    good = _frame() * 10
    assert _usable(good) == len(good)
    assert _usable(good + _frame()[:500]) == len(good)                     # khung cut do
    assert _usable(good + b'TAG' + b'x' * 125) == len(good)                # ID3v1 sau khung tron ven
    assert _usable(good + b'\x00' * (Api.GN_TAIL_SLACK + 1)) is None       # rac dai: ma hoa lai


@pytest.mark.parametrize('cut', [300, 498, 500, 520, 600, 625])
def test_truncated_frame_swallowing_id3v1_is_dropped(cut):
    # Khung cut do + 128 byte ID3v1 co the >= mot khung (626 byte): khong duoc
    # coi la khung tron ven, neu khong no se nuot khung dau cua bai noi sau.
    good = _frame() * 10
    assert _usable(good + _frame()[:cut] + b'TAG' + b'x' * 125) == len(good)


def test_scan_accepts_frame_followed_by_cut_header_at_eof():
    good = _frame() * 3
    offs, problem, _ = Api._gn_mp3_scan(good + b'\xff', 44100, 2)
    assert problem == 'partial' and offs[-1] == len(good)


def test_copy_usable_rejects_bad_first_frame_format_and_empty():
    assert _usable(_frame(mdb=12) + _frame() * 3) is None
    assert _usable(_frame() * 3 + _frame(sr=32000) * 3) is None
    assert _usable(b'') is None
    assert _usable(b'garbage' * 10) is None


# ── _gn_copy_starts / _gn_track_secs ────────────────────────────────

def test_copy_starts_matches_measured_output():
    # So do that 2026-10-16 (giai ma file noi, tim tung bai theo PCM):
    # roa (khong the, 7918 khung) -> moonlight (the LAME, 9738) -> Luke ma hoa lai.
    starts, total = Api._gn_copy_starts([(7918, 0), (9738, 1105), (7225, 1105)])
    assert starts == [0, 9122112, 20340288]
    assert total == (7918 + 9738 + 7225) * SPF - Api.GN_DEC_DELAY


def test_copy_starts_first_song_with_lame_tag():
    starts, total = Api._gn_copy_starts([(100, 1105), (50, 0)])
    assert starts == [1105 - 529, 100 * SPF - 529]
    assert total == 150 * SPF - 529


def test_copy_starts_empty():
    assert Api._gn_copy_starts([]) == ([], 0)


def test_track_secs_cumulative_sums_hit_starts():
    starts, total = Api._gn_copy_starts([(5131, 1105), (7853, 0), (8157, 1105), (4862, 0)])
    secs = Api._gn_track_secs(starts, total, 44100)
    cum = 0.0
    for i, s in enumerate(secs):
        if i > 0:
            assert cum * 44100 == pytest.approx(starts[i], abs=1e-6)
        cum += s
    assert cum * 44100 == pytest.approx(total, abs=1e-6)


def test_tracklist_start_column_uses_real_starts():
    starts, total = Api._gn_copy_starts([(9000, 1105), (8000, 0), (7000, 1105)])
    secs = Api._gn_track_secs(starts, total, 44100)
    txt = Api._concat_music_tracklist('x.mp3', list(zip(['a', 'b', 'c'], secs)))
    rows = [ln.split() for ln in txt.splitlines() if ln[:4].strip().isdigit()]
    want = [Api._fmt_hms(math.floor(s / 44100 + 1e-6)) for s in [0] + starts[1:]]
    assert [r[1] for r in rows] == want


# ── _gn_bed_chunks ──────────────────────────────────────────────────

@pytest.mark.parametrize('raw, cpu, n', [
    ('auto', 12, 11), ('auto', 2, 2), ('auto', 1, 2), ('auto', 64, 16), (None, None, 2),
    ('', 8, 7), ('abc', 8, 7), (0, 8, 7), (-3, 8, 7),
    (6, 12, 6), ('6', 12, 6), (1, 12, 1), (100, 12, 32),
])
def test_bed_chunks(raw, cpu, n):
    assert Api._gn_bed_chunks(raw, cpu) == n


def test_preset_has_bed_encode_chunks_auto():
    p = os.path.join(os.path.dirname(_HERE), 'bin', 'codes', 'Ghep nhac Jazz', 'Ghep nhac dai.json')
    with open(p, encoding='utf-8') as fh:
        code = json.load(fh)
    assert code['type'] == 'concat_music'
    assert code['bed_encode_chunks'] == 'auto'


# ── _gn_chunk_plan ──────────────────────────────────────────────────

def _check_plan(total, n, pre=4, post=4, min_frames=256):
    plan = Api._gn_chunk_plan(total, n, SPF, pre, post, min_frames)
    fr = total // SPF
    assert 1 <= len(plan) <= max(1, n)
    assert plan[0]['a'] == 0 and plan[0]['in0'] == 0 and plan[0]['skip'] == 0
    assert plan[-1]['keep'] is None and plan[-1]['in1'] == total
    for k, c in enumerate(plan):
        assert 0 <= c['in0'] < c['in1'] <= total                 # roll khong vuot hai dau
        assert c['in0'] % SPF == 0                                # bien khung trong thoi gian ra
        assert c['in0'] == (c['a'] - c['skip']) * SPF
        if k > 0:
            assert c['skip'] == pre
        if k < len(plan) - 1:
            nxt = plan[k + 1]
            assert c['a'] + c['keep'] == nxt['a']                 # khong ho, khong chong
            assert c['in1'] == (nxt['a'] + post) * SPF
            assert c['in1'] % SPF == 0
            assert c['keep'] >= min_frames
    if len(plan) > 1:
        assert fr - plan[-1]['a'] >= min_frames                   # khuc cuoi du dai cho post-roll
    return plan


@pytest.mark.parametrize('total', [
    226508091,                     # 85,6 phut that
    44100 * 3600 * 3,              # 3 tieng
    SPF * 5000,                    # dung boi so khung
    SPF * 5000 + 1151,
    SPF * (2 * 256 + 4) + 7,       # vua du 2 khuc
    SPF * (2 * 256 + 3),           # thieu 1 khung -> 1 khuc
    44100 * 60,
    1000,                          # < 1 khung
    1,
])
@pytest.mark.parametrize('n', [1, 2, 11, 16])
def test_chunk_plan_invariants(total, n):
    _check_plan(total, n)


def test_chunk_plan_real_list_eleven_chunks():
    plan = _check_plan(226508091, 11)
    assert len(plan) == 11
    sizes = [c['keep'] for c in plan[:-1]]
    assert max(sizes) - min(sizes) <= 1                          # chia deu


def test_chunk_plan_short_total_single_chunk_keeps_everything():
    plan = Api._gn_chunk_plan(44100 * 5, 11)
    assert plan == [{'a': 0, 'in0': 0, 'in1': 44100 * 5, 'skip': 0, 'keep': None}]


def test_chunk_plan_limits_count_by_min_frames():
    total = SPF * (10 * 256 + 4)
    assert len(Api._gn_chunk_plan(total, 16)) == 10


# ── _gn_chunk_inputs ────────────────────────────────────────────────

@pytest.mark.parametrize('in0, in1, want', [
    (0, 600, (0, 3, 0)),
    (150, 250, (1, 2, 50)),
    (100, 300, (1, 2, 0)),
    (100, 301, (1, 3, 0)),
    (299, 301, (1, 3, 199)),
    (550, 600, (2, 3, 250)),
    (0, 1, (0, 1, 0)),
])
def test_chunk_inputs(in0, in1, want):
    assert Api._gn_chunk_inputs([100, 200, 300], in0, in1) == want


def test_chunk_inputs_cover_requested_range():
    lens = [9121536, 11211510, 8319000, 13754592, 5904798]
    cum = [0]
    for x in lens:
        cum.append(cum[-1] + x)
    for in0, in1 in [(0, 5000), (9121536, 9121537), (20000000, 30000000), (cum[-1] - 10, cum[-1])]:
        j, m, off = Api._gn_chunk_inputs(lens, in0, in1)
        assert cum[j] + off == in0 and 0 <= off < lens[j]
        assert cum[m] >= in1 and cum[m - 1] < in1
