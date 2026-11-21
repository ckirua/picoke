"""UTF-8 JSON document file I/O."""

import json
from os import PathLike


def _reject_constant(token: str) -> None:
    raise ValueError(f'{token} is not a JSON value')


def read_json(path: str | PathLike[str]) -> object:
    """Read one strict UTF-8 JSON document."""
    with open(path, encoding='utf-8') as stream:
        return json.load(stream, parse_constant=_reject_constant)


def write_json(path: str | PathLike[str], value: object) -> None:
    """Overwrite a file with one compact JSON value and a trailing LF."""
    text = json.dumps(value, separators=(',', ':'), allow_nan=False)
    with open(path, 'w', encoding='utf-8', newline='\n') as stream:
        stream.write(text)
        stream.write('\n')
