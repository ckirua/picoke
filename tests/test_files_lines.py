import pytest

from picoke.io.files import iter_lines


def test_filtering_order_and_universal_newlines(tmp_path):
    path = tmp_path / 'lines.txt'
    path.write_bytes(b'  #c\r\n   \r\n x \r\ny')
    assert list(iter_lines(path, strip=True, skip_empty=True, comment_prefix='#')) == ['x', 'y']


def test_defaults_preserve_whitespace_and_strip_only_terminator(tmp_path):
    path = tmp_path / 'lines.txt'
    path.write_bytes(b' x \r\n  \rbare\r')
    assert list(iter_lines(path)) == [' x ', '  ', 'bare']


def test_empty_file(tmp_path):
    path = tmp_path / 'empty.txt'
    path.write_bytes(b'')
    assert list(iter_lines(path)) == []


def test_open_is_lazy_until_first_iteration(tmp_path):
    missing = tmp_path / 'missing.txt'
    lines = iter_lines(missing)
    assert iter(lines) is lines
    with pytest.raises(FileNotFoundError):
        next(lines)


def test_close_early_releases_file(tmp_path):
    path = tmp_path / 'lines.txt'
    path.write_text('first\nsecond\n', encoding='utf-8')
    lines = iter_lines(path)
    assert next(lines) == 'first'
    stream = lines.gi_frame.f_locals['stream']
    assert not stream.closed
    lines.close()
    assert stream.closed


def test_empty_comment_prefix_fails_before_open(tmp_path):
    lines = iter_lines(tmp_path / 'missing.txt', comment_prefix='')
    with pytest.raises(ValueError, match='comment_prefix'):
        next(lines)


def test_strict_decode_error_closes_generator(tmp_path):
    path = tmp_path / 'invalid.txt'
    path.write_bytes(b'ok\n\xff')
    lines = iter_lines(path)
    with pytest.raises(UnicodeDecodeError):
        next(lines)
    with pytest.raises(StopIteration):
        next(lines)


def test_skip_empty_and_comments_without_strip(tmp_path):
    path = tmp_path / 'lines.txt'
    path.write_text('\n #comment\n#comment\n   \n', encoding='utf-8')
    assert list(iter_lines(path, skip_empty=True, comment_prefix='#')) == [' #comment', '   ']
