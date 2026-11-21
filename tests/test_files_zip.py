from __future__ import annotations

import warnings
import zipfile
from io import BytesIO

import pytest

from picoke.io.files.zip import ZipEntry, read_zip


def make_archive(path, members):
    with zipfile.ZipFile(path, 'w') as archive:
        for name, data in members:
            archive.writestr(name, data)


def test_filters_preserve_archive_order_and_do_not_extract(tmp_path):
    path = tmp_path / 'members.zip'
    make_archive(path, [('root.txt', b'root'), ('data/a.txt', b'a'), ('data/b.bin', b'b'), ('data/', b'')])

    entries = read_zip(path, names=['root.txt', 'data/b.bin', 'data/a.txt', 'data/'], prefix='data/')

    assert entries == [ZipEntry('data/a.txt', b'a'), ZipEntry('data/b.bin', b'b')]
    assert not (tmp_path / 'data').exists()
    assert read_zip(path, names=['missing']) == []


def test_duplicate_selected_names_rejected_but_unselected_duplicates_ignored(tmp_path):
    path = tmp_path / 'duplicates.zip'
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', UserWarning)
        make_archive(path, [('wanted', b'a'), ('other', b'x'), ('wanted', b'b')])

    with pytest.raises(ValueError, match='duplicate'):
        read_zip(path, names=['wanted'])
    assert read_zip(path, names=['other']) == [ZipEntry('other', b'x')]


@pytest.mark.parametrize(
    ('limits', 'message'),
    [
        ({'max_members': 0}, 'max_members'),
        ({'max_member_size': 1}, 'max_member_size'),
        ({'max_total_size': 2}, 'max_total_size'),
    ],
)
def test_advertised_limits_checked_before_payload_read(tmp_path, limits, message):
    path = tmp_path / 'limits.zip'
    make_archive(path, [('a', b'ab'), ('b', b'c')])
    with pytest.raises(ValueError, match=message):
        read_zip(path, **limits)


@pytest.mark.parametrize('field', ['max_members', 'max_member_size', 'max_total_size'])
def test_limits_require_nonnegative_exact_int(tmp_path, field):
    path = tmp_path / 'empty.zip'
    make_archive(path, [])
    with pytest.raises(TypeError):
        read_zip(path, **{field: True})
    with pytest.raises(ValueError):
        read_zip(path, **{field: -1})


def test_names_and_prefix_validation(tmp_path):
    path = tmp_path / 'empty.zip'
    make_archive(path, [])
    with pytest.raises(TypeError):
        read_zip(path, names='a')
    with pytest.raises(TypeError):
        read_zip(path, names=[1])
    with pytest.raises(TypeError):
        read_zip(path, prefix=1)


def test_crc_failure_propagates(tmp_path):
    path = tmp_path / 'corrupt.zip'
    make_archive(path, [('name', b'payload')])
    raw = path.read_bytes()
    path.write_bytes(raw.replace(b'payload', b'Payload', 1))

    with pytest.raises(zipfile.BadZipFile, match='CRC'):
        read_zip(path)


def test_actual_output_guard(tmp_path, monkeypatch):
    path = tmp_path / 'members.zip'
    make_archive(path, [('entry', b'')])

    class OverproducingMember(BytesIO):
        def read(self, size=-1):
            assert size == 1
            return super().read(size)

    monkeypatch.setattr(zipfile.ZipFile, 'open', lambda *_: OverproducingMember(b'x'))
    with pytest.raises(ValueError, match='max_member_size'):
        read_zip(path, max_member_size=0)
    with pytest.raises(ValueError, match='max_total_size'):
        read_zip(path, max_total_size=0)
