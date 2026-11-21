"""Bounded reads of selected ZIP archive members."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from os import PathLike
from zipfile import ZipFile, ZipInfo


@dataclass(frozen=True, slots=True)
class ZipEntry:
    """A selected archive member and its uncompressed contents."""

    name: str
    data: bytes


def read_zip(
    path: str | PathLike[str],
    *,
    names: Collection[str] | None = None,
    prefix: str | None = None,
    max_members: int = 1024,
    max_member_size: int = 16 * 1024**2,
    max_total_size: int = 64 * 1024**2,
) -> list[ZipEntry]:
    """Read selected ZIP members, enforcing advertised and actual size limits."""
    for label, limit in (
        ('max_members', max_members),
        ('max_member_size', max_member_size),
        ('max_total_size', max_total_size),
    ):
        if type(limit) is not int:
            raise TypeError(f'{label} must be an exact int')
        if limit < 0:
            raise ValueError(f'{label} must be nonnegative')

    selected_names: set[str] | None = None
    if names is not None:
        if isinstance(names, (str, bytes)):
            raise TypeError('names must be a collection of strings, not str or bytes')
        selected_names = set()
        for name in names:
            if not isinstance(name, str):
                raise TypeError('names elements must be strings')
            selected_names.add(name)
    if prefix is not None and not isinstance(prefix, str):
        raise TypeError('prefix must be a string or None')

    with ZipFile(path) as archive:
        selected: list[ZipInfo] = []
        seen: set[str] = set()
        advertised_total = 0
        for info in archive.infolist():
            if info.is_dir():
                continue
            if selected_names is not None and info.filename not in selected_names:
                continue
            if prefix is not None and not info.filename.startswith(prefix):
                continue
            if info.filename in seen:
                raise ValueError(f'duplicate selected ZIP member name: {info.filename!r}')
            seen.add(info.filename)
            selected.append(info)
            if len(selected) > max_members:
                raise ValueError('selected ZIP member count exceeds max_members')
            if info.file_size > max_member_size:
                raise ValueError(f'ZIP member exceeds max_member_size: {info.filename!r}')
            advertised_total += info.file_size
            if advertised_total > max_total_size:
                raise ValueError('selected ZIP member sizes exceed max_total_size')

        entries: list[ZipEntry] = []
        actual_total = 0
        for info in selected:
            parts: list[bytes] = []
            member_size = 0
            with archive.open(info) as member:
                while True:
                    member_remaining = max_member_size - member_size
                    total_remaining = max_total_size - actual_total
                    chunk = member.read(min(64 * 1024, member_remaining + 1, total_remaining + 1))
                    if not chunk:
                        break
                    size = len(chunk)
                    if size > member_remaining:
                        raise ValueError(f'ZIP member exceeds max_member_size: {info.filename!r}')
                    if size > total_remaining:
                        raise ValueError('actual ZIP member data exceeds max_total_size')
                    parts.append(chunk)
                    member_size += size
                    actual_total += size
            entries.append(ZipEntry(info.filename, b''.join(parts)))
        return entries
