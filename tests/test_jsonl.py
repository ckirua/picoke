import json

import pytest

from picoke.io.files import JSONLDecodeError, read_jsonl, write_jsonl


def test_reads_lf_crlf_and_unterminated_utf8(tmp_path):
    path = tmp_path / 'records.jsonl'
    path.write_bytes('null\r\n{"city":"東京"}\n[1,true]'.encode())
    with_values = list(read_jsonl(path))
    assert with_values == [None, {'city': '東京'}, [1, True]]


@pytest.mark.parametrize('bad', [b'\n', b'  \t\r\n', b'{} {}\n', b'NaN\n', b'\xff\n'])
def test_invalid_record_fails_at_physical_line(tmp_path, bad):
    path = tmp_path / 'records.jsonl'
    path.write_bytes(b'true\r\n' + bad + b'0\n')
    records = read_jsonl(path)
    assert next(records) is True
    with pytest.raises(JSONLDecodeError, match=r'^line 2:') as error:
        next(records)
    assert error.value.line_number == 2


def test_bom_preserves_stdlib_decode_error(tmp_path):
    path = tmp_path / 'records.jsonl'
    line = '\ufeff{}\n'
    path.write_bytes(b'1\n' + line.encode())
    with pytest.raises(json.JSONDecodeError) as stdlib_error:
        json.loads(line)
    with pytest.raises(JSONLDecodeError) as error:
        list(read_jsonl(path))
    assert error.value.line_number == 2
    cause = error.value.__cause__
    assert isinstance(cause, json.JSONDecodeError)
    assert (cause.msg, cause.doc, cause.pos) == (stdlib_error.value.msg, line, 0)


def test_writer_overwrites_and_consumes_records_once(tmp_path):
    path = tmp_path / 'records.jsonl'
    path.write_bytes(b'stale contents that must not remain')
    values = [{'message': 'café\n雪'}, [], None, False]
    write_jsonl(path, iter(values))
    lines = path.read_bytes().splitlines(keepends=True)
    assert lines[1:] == [b'[]\n', b'null\n', b'false\n']
    assert lines[0].endswith(b'\n') and b'": ' not in lines[0]
    assert json.loads(lines[0]) == values[0]
    assert list(read_jsonl(path)) == values


def test_serialization_failure_preserves_prior_complete_lines(tmp_path):
    path = tmp_path / 'records.jsonl'
    with pytest.raises(ValueError):
        write_jsonl(path, [1, {'bad': float('inf')}, 3])
    assert path.read_bytes() == b'1\n'


def test_iterator_failure_preserves_prior_complete_lines(tmp_path):
    path = tmp_path / 'records.jsonl'

    def records():
        yield {'city': '雪'}
        raise RuntimeError('input failed')

    with pytest.raises(RuntimeError, match='input failed'):
        write_jsonl(path, records())
    assert path.read_bytes() == b'{"city":"\\u96ea"}\n'


def test_empty_file_and_missing_path(tmp_path):
    path = tmp_path / 'records.jsonl'
    records = read_jsonl(path)
    with pytest.raises(FileNotFoundError):
        next(records)
    path.write_bytes(b'stale contents')
    write_jsonl(path, ())
    assert path.read_bytes() == b''
    assert list(read_jsonl(path)) == []


def test_closing_reader_early_closes_file(tmp_path):
    path = tmp_path / 'records.jsonl'
    path.write_bytes(b'1\n2\n')
    records = read_jsonl(path)
    assert next(records) == 1
    stream = records.gi_frame.f_locals['stream']
    assert not stream.closed
    records.close()
    assert stream.closed
