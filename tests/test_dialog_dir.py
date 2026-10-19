"""Tests cho Api._resolve_dialog_dir - thu muc mo dau cua cac nut duyet.

Nap RENUP_gui.py theo duong dan (file khong phai package), giong
test_concat_music.py. Ham thuan: khong GUI, khong evaluate_js.
"""
import importlib.util
import os

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(os.path.dirname(_HERE), 'RENUP_gui.py')
_spec = importlib.util.spec_from_file_location('renup_gui', _SRC)
renup_gui = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(renup_gui)

resolve = renup_gui.Api._resolve_dialog_dir
HOME = r'C:\Users\Someone'


def test_existing_dir_is_itself(tmp_path):
    assert resolve(str(tmp_path), HOME) == str(tmp_path)


def test_existing_file_gives_its_folder(tmp_path):
    f = tmp_path / 'song.mp3'
    f.write_bytes(b'')
    assert resolve(str(f), HOME) == str(tmp_path)


def test_missing_subfolder_walks_up_to_existing_ancestor(tmp_path):
    missing = tmp_path / 'output' / 'chua_tao'
    assert resolve(str(missing), HOME) == str(tmp_path)


def test_trailing_separator_and_quotes_are_tolerated(tmp_path):
    assert resolve(f'  "{tmp_path}{os.sep}"  ', HOME) == str(tmp_path)


@pytest.mark.parametrize('value', [None, '', '   ', '""', "''", ' " " '])
def test_empty_gives_home(value):
    assert resolve(value, HOME) == HOME


@pytest.mark.parametrize('value', ['::', 'abc', r'\Users\Admin', 'Q:'])
def test_garbage_or_relative_gives_home(value):
    # r'\Users\Admin' chinh la HOMEPATH khong co o dia - goc cua loi.
    assert resolve(value, HOME) == HOME
