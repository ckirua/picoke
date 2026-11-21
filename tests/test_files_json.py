import json

import pytest

from picoke.io.files import read_json, write_json


def test_round_trips_utf8_json(tmp_path):
    path = tmp_path / 'document.json'
    write_json(path, {'city': '雪'})
    assert path.read_bytes() == b'{"city":"\\u96ea"}\n'
    assert read_json(path) == {'city': '雪'}
    path.write_text('{"city":"雪"}', encoding='utf-8')
    assert read_json(path) == {'city': '雪'}


def test_rejects_second_json_value(tmp_path):
    path = tmp_path / 'document.json'
    path.write_text('{} {}', encoding='utf-8')
    with pytest.raises(json.JSONDecodeError):
        read_json(path)


@pytest.mark.parametrize('contents', [b'NaN', b'Infinity', b'-Infinity'])
def test_rejects_non_finite_constants(tmp_path, contents):
    path = tmp_path / 'document.json'
    path.write_bytes(contents)
    with pytest.raises(ValueError):
        read_json(path)


def test_invalid_utf8_propagates(tmp_path):
    path = tmp_path / 'document.json'
    path.write_bytes(b'\xff')
    with pytest.raises(UnicodeDecodeError):
        read_json(path)


def test_serialization_failure_preserves_existing_file(tmp_path):
    path = tmp_path / 'document.json'
    path.write_bytes(b'previous contents')
    with pytest.raises(ValueError):
        write_json(path, float('inf'))
    assert path.read_bytes() == b'previous contents'
