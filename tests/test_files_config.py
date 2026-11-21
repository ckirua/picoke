"""Tests for TOML and YAML document file utilities."""

import tomllib

import yaml
import pytest

from picoke.io.files import read_toml, read_yaml, write_toml, write_yaml


def test_toml_read_and_write(tmp_path):
    source = tmp_path / 'input.toml'
    source.write_text('[place]\ncity = "Oslo"\n', encoding='utf-8')
    value = read_toml(source)
    assert value == {'place': {'city': 'Oslo'}}

    target = tmp_path / 'output.toml'
    write_toml(target, value)
    assert read_toml(target) == value


def test_toml_decode_error_propagates(tmp_path):
    source = tmp_path / 'invalid.toml'
    source.write_text('invalid = [', encoding='utf-8')
    with pytest.raises(tomllib.TOMLDecodeError):
        read_toml(source)


def test_toml_serialization_failure_preserves_destination(tmp_path):
    target = tmp_path / 'existing.toml'
    target.write_bytes(b'old contents')
    with pytest.raises((TypeError, ValueError)):
        write_toml(target, {'unsupported': object()})
    assert target.read_bytes() == b'old contents'


def test_yaml_unicode_round_trip(tmp_path):
    target = tmp_path / 'document.yaml'
    value = {'city': '雪', 'nested': {'enabled': True}}
    write_yaml(target, value)
    assert '雪' in target.read_text(encoding='utf-8')
    assert read_yaml(target) == value


def test_yaml_rejects_unsafe_tags_and_multiple_documents(tmp_path):
    target = tmp_path / 'invalid.yaml'
    target.write_text('!!python/object:example.Value {}', encoding='utf-8')
    with pytest.raises(yaml.YAMLError):
        read_yaml(target)

    target.write_text('first: 1\n---\nsecond: 2\n', encoding='utf-8')
    with pytest.raises(yaml.YAMLError):
        read_yaml(target)


def test_yaml_serialization_failure_preserves_destination(tmp_path):
    target = tmp_path / 'existing.yaml'
    target.write_bytes(b'old contents')
    with pytest.raises(yaml.YAMLError):
        write_yaml(target, object())
    assert target.read_bytes() == b'old contents'
