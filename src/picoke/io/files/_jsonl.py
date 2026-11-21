"""UTF-8 JSON Lines file I/O."""

import json
from collections.abc import Generator, Iterable
from os import PathLike

from ._json import _reject_constant


class JSONLDecodeError(ValueError):
    """Invalid UTF-8 or JSON in a physical line, numbered from one."""

    def __init__(self, line_number: int, message: str) -> None:
        self.line_number = line_number
        super().__init__(f'line {line_number}: {message}')


def read_jsonl(path: str | PathLike[str]) -> Generator[object]:
    """Yield one value per physical line; close the generator if stopping early."""
    with open(path, 'rb') as stream:
        decode = json.JSONDecoder(parse_constant=_reject_constant).decode
        for number, line in enumerate(stream, 1):
            try:
                text = line.decode('utf-8')
                # JSONDecoder.decode omits json.loads' specific BOM diagnostic.
                record = json.loads(text) if text.startswith('\ufeff') else decode(text)
            except ValueError as exc:
                raise JSONLDecodeError(number, str(exc)) from exc
            yield record


def write_jsonl(path: str | PathLike[str], records: Iterable[object]) -> None:
    """Overwrite a file with one compact JSON value per line, non-atomically."""
    with open(path, 'w', encoding='utf-8', newline='\n') as stream:
        encode = None
        for record in records:
            if encode is None:
                # Empty writes only truncate the file; they need no encoder.
                encode = json.JSONEncoder(separators=(',', ':'), allow_nan=False).encode
            stream.write(encode(record) + '\n')
