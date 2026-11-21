"""TOML document file I/O."""

import tomllib
from collections.abc import Mapping
from os import PathLike
from typing import Any


def read_toml(path: str | PathLike[str]) -> dict[str, Any]:
    """Read a TOML document from a binary file."""
    with open(path, 'rb') as stream:
        return tomllib.load(stream)


def write_toml(path: str | PathLike[str], value: Mapping[str, object]) -> None:
    """Serialize a TOML document before overwriting its destination."""
    try:
        import tomli_w
    except ModuleNotFoundError as exc:
        if exc.name != 'tomli_w':
            raise
        raise ImportError('write_toml requires picoke[toml]') from exc

    document = tomli_w.dumps(value)
    with open(path, 'w', encoding='utf-8', newline='') as stream:
        stream.write(document)
