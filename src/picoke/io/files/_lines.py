"""Text line iteration helpers."""

from collections.abc import Generator
from os import PathLike


def iter_lines(
    path: str | PathLike[str],
    *,
    encoding: str = 'utf-8',
    strip: bool = False,
    skip_empty: bool = False,
    comment_prefix: str | None = None,
) -> Generator[str]:
    """Yield text lines lazily, optionally stripping/filtering each line."""
    if comment_prefix == '':
        raise ValueError('comment_prefix cannot be empty')
    with open(path, 'r', encoding=encoding, errors='strict', newline=None) as stream:
        for line in stream:
            if line.endswith('\n'):
                line = line[:-1]
            if strip:
                line = line.strip()
            if skip_empty and line == '':
                continue
            if comment_prefix is not None and line.startswith(comment_prefix):
                continue
            yield line
