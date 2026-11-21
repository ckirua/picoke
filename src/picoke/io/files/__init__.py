"""File-format utilities."""

from ._json import read_json, write_json
from ._jsonl import JSONLDecodeError, read_jsonl, write_jsonl
from ._lines import iter_lines
from ._toml import read_toml, write_toml
from ._yaml import read_yaml, write_yaml

__all__ = (
    'JSONLDecodeError',
    'iter_lines',
    'read_json',
    'read_jsonl',
    'read_toml',
    'read_yaml',
    'write_json',
    'write_jsonl',
    'write_toml',
    'write_yaml',
)
