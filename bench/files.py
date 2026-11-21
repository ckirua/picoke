"""Warm-cache API comparisons on Linux; run with an installed picoke[files]."""

import json
import platform
import sys
import tomllib
import tracemalloc
from functools import partial
from importlib.metadata import version
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory
from time import perf_counter
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

import tomli_w
import yaml
from picop.cybytes import bytes_len
from picop.cylist import list_append
from picop.cylong import long_check_exact
from picop.cyset import set_add, set_contains
from picop.cystr import str_endswith, str_startswith

from picoke.io import files
from picoke.io.files.zip import ZipEntry, read_zip


SAMPLES = 7
JSON_OPTIONS = {'separators': (',', ':'), 'allow_nan': False}
BATCH = 64 * 1024


def reject_constant(token):
    raise ValueError(f'{token} is not a JSON value')


def json_read(path):
    with open(path, encoding='utf-8') as stream:
        return json.load(stream, parse_constant=reject_constant)


def document_write(path, value, dump):
    text = dump(value)
    with open(path, 'w', encoding='utf-8', newline='\n') as stream:
        stream.write(text)


def jsonl_read(path):
    with open(path, 'rb') as stream:
        # Before decoder reuse, with matching JSONL framing and diagnostics.
        records = []
        for number, line in enumerate(stream, 1):
            try:
                value = json.loads(line.decode('utf-8'), parse_constant=reject_constant)
            except ValueError as exc:
                raise files.JSONLDecodeError(number, str(exc)) from exc
            records.append(value)
        return records


def jsonl_write(path, values):
    with open(path, 'w', encoding='utf-8', newline='\n') as stream:
        for value in values:
            stream.write(json.dumps(value, separators=(',', ':'), allow_nan=False) + '\n')


def jsonl_batch(path, values):
    with path.open('w', encoding='utf-8', newline='\n') as stream:
        pending = []
        size = 0

        def flush():
            text = ''.join(pending)
            pending.clear()  # Do not retry an I/O failure in the cleanup path.
            stream.write(text)

        try:
            for value in values:
                line = json.dumps(value, separators=(',', ':'), allow_nan=False) + '\n'
                # dumps emits ASCII here, so characters equal encoded bytes.
                if len(line) + size > BATCH and pending:
                    flush()
                    size = 0
                if len(line) > BATCH:
                    stream.write(line)
                else:
                    pending.append(line)
                    size += len(line)
        finally:
            if pending:
                flush()


def check_batch(root):
    def broken_input():
        yield 'x' * BATCH
        yield '雪'
        raise RuntimeError('input failed')

    for make_values, expected_error in ((broken_input, RuntimeError), (lambda: [1, object()], TypeError)):
        outputs = []
        for arm in (files.write_jsonl, jsonl_batch):
            path = root / 'failed.jsonl'
            try:
                arm(path, make_values())
            except expected_error:
                outputs.append(path.read_bytes())
            else:
                raise AssertionError('writer suppressed input error')
        assert outputs[0] == outputs[1]


def read_config(path, *, binary, load):
    if binary:
        with path.open('rb') as stream:
            return load(stream)
    with path.open(encoding='utf-8') as stream:
        return load(stream)


def lines_direct(path):
    with path.open(encoding='utf-8') as stream:
        return [line for raw in stream if (line := raw.strip()) and not line.startswith('#')]


def lines_bound(path, *, encoding='utf-8', strip=False, skip_empty=False, comment_prefix=None):
    if comment_prefix == '':
        raise ValueError('comment_prefix cannot be empty')
    endswith, startswith, strip_text = str.endswith, str.startswith, str.strip
    with open(path, 'r', encoding=encoding, errors='strict', newline=None) as stream:
        for line in stream:
            if endswith(line, '\n'):
                line = line[:-1]
            if strip:
                line = strip_text(line)
            if skip_empty and line == '':
                continue
            if comment_prefix is not None and startswith(line, comment_prefix):
                continue
            yield line


def lines_picop(path, *, encoding='utf-8', strip=False, skip_empty=False, comment_prefix=None):
    if comment_prefix == '':
        raise ValueError('comment_prefix cannot be empty')
    with open(path, 'r', encoding=encoding, errors='strict', newline=None) as stream:
        for line in stream:
            if str_endswith(line, '\n'):
                line = line[:-1]
            if strip:
                line = line.strip()
            if skip_empty and line == '':
                continue
            if comment_prefix is not None and str_startswith(line, comment_prefix):
                continue
            yield line


def check_lines(root):
    path = root / 'line-edges.txt'
    path.write_bytes('  #c\r\n   \r\n snow 雪 \ry'.encode())
    for strip in (False, True):
        for skip in (False, True):
            for prefix in (None, '#'):
                options = dict(strip=strip, skip_empty=skip, comment_prefix=prefix)
                expected = list(files.iter_lines(path, **options))
                assert list(lines_bound(path, **options)) == expected
                assert list(lines_picop(path, **options)) == expected


def zip_direct(path, prefix):
    with ZipFile(path) as archive:
        return [ZipEntry(info.filename, archive.read(info)) for info in archive.infolist()
                if not info.is_dir() and info.filename.startswith(prefix)]


def zip_limit_setup(limits, *, picop):
    if picop:
        for label, limit in limits:
            if not long_check_exact(limit):
                raise TypeError(label)
            if limit < 0:
                raise ValueError(label)
    else:
        for label, limit in limits:
            if type(limit) is not int:
                raise TypeError(label)
            if limit < 0:
                raise ValueError(label)


def zip_container_ops(names, entries, *, picop):
    selected = set()
    seen = set(names)
    result = []
    total = 0
    if picop:
        for name in names:
            set_add(selected, name)
        for entry in entries:
            if set_contains(seen, entry.name) and set_contains(selected, entry.name):
                list_append(result, entry)
                total += bytes_len(entry.data)
    else:
        for name in names:
            selected.add(name)
        for entry in entries:
            if entry.name in seen and entry.name in selected:
                result.append(entry)
                total += len(entry.data)
    return result, total


def sequence(per_call, repeat):
    elapsed = 0.0
    for _ in range(repeat):
        start = perf_counter()
        result = per_call()
        elapsed += perf_counter() - start
        del result
    return elapsed / repeat


def peak(arm, check):
    tracemalloc.start()
    try:
        result = arm()
        size = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    check(result)
    return size


def compare(label, control, trial, check, units, *, scale=1):
    for arm in (control, trial):
        check(arm())  # One warm-up per arm; validate outside the timing.
    reference, candidate = [], []
    repeats = max(1, 256 // scale)
    for index in range(SAMPLES):
        arms = ((control, reference), (trial, candidate))
        for arm, timings in arms if index % 2 == 0 else reversed(arms):
            timings.append(sequence(arm, repeats))
    base, new = median(reference), median(candidate)
    base_peak, new_peak = peak(control, check), peak(trial, check)
    wins = sum(t < c for c, t in zip(reference, candidate, strict=True))
    ratios = [round(t / c, 3) for c, t in zip(reference, candidate, strict=True)]
    print(f'{label}: {base * 1e6:.2f}/{new * 1e6:.2f} us; '
          f'{units / base:.0f}/{units / new:.0f} units/s; '
          f'{base_peak}/{new_peak} B peak; ratio {new / base:.3f}, wins {wins}/7; pairs {ratios}')


def equality(expected):
    def check(actual):
        assert actual == expected
    return check


def profile(root, n, word, charset):
    rows = [{'id': i, 'text': f'{word} {i}'} for i in range(n)]
    document = {'records': rows}
    scale = n // 32
    stages = (
        ('json', document, json_read,
         partial(document_write, dump=lambda value: json.dumps(value, **JSON_OPTIONS) + '\n'),
         files.read_json, files.write_json),
        ('jsonl', rows, jsonl_read, jsonl_write,
         lambda path: list(files.read_jsonl(path)), files.write_jsonl),
        ('toml', document, partial(read_config, binary=True, load=tomllib.load),
         partial(document_write, dump=tomli_w.dumps), files.read_toml, files.write_toml),
        ('yaml', document, partial(read_config, binary=False, load=yaml.safe_load),
         partial(document_write, dump=partial(yaml.safe_dump, allow_unicode=True, sort_keys=False)),
         files.read_yaml, files.write_yaml),
    )
    for fmt, value, direct_read, direct_write, api_read, api_write in stages:
        path = root / f'input.{fmt}'
        out = root / f'output.{fmt}'
        direct_write(path, value)
        raw = path.read_bytes()

        def check_write(_):
            assert out.read_bytes() == raw

        # PyYAML is much heavier than the others; do not oversample it.
        repeats_scale = scale * (32 if fmt == 'yaml' else 4 if fmt == 'toml' else 1)
        label = f'{fmt} {n} {charset}'
        compare(f'{label} read', partial(direct_read, path), partial(api_read, path),
                equality(value), n, scale=repeats_scale)
        compare(f'{label} write', partial(direct_write, out, value), partial(api_write, out, value),
                check_write, n, scale=repeats_scale)
        if fmt == 'jsonl':
            compare(f'{label} writer batching', partial(api_write, out, value), partial(jsonl_batch, out, value),
                    check_write, n, scale=scale)

    path = root / 'lines.txt'
    items = [f'{word} {i}' for i in range(n)]
    path.write_bytes(''.join(('  # omit  ', '  ', f'  {item}  ', f'  {item}  ')[i % 4]
                             + ('\r\n' if i % 2 else '\r') for i, item in enumerate(items)).encode())
    expected = equality([item for i, item in enumerate(items) if i % 4 >= 2])
    filters = dict(strip=True, skip_empty=True, comment_prefix='#')

    def api_lines():
        return list(files.iter_lines(path, **filters))

    compare(f'lines {n} {charset} read', partial(lines_direct, path), api_lines, expected, n, scale=scale)
    compare(f'lines {n} {charset} local binding', api_lines,
            lambda: list(lines_bound(path, **filters)), expected, n, scale=scale)
    compare(f'lines {n} {charset} picop predicates', api_lines,
            lambda: list(lines_picop(path, **filters)), expected, n, scale=scale)


def archives(root):
    for method, mode in ((ZIP_STORED, 'stored'), (ZIP_DEFLATED, 'deflated')):
        for size in (256, 64 * 1024):
            path = root / 'archive.zip'
            entries = [ZipEntry(f'data/{i:02}.bin', bytes((i,)) * size) for i in range(32)]
            with ZipFile(path, 'w', compression=method) as archive:
                archive.writestr('data/', b'')
                archive.writestr('other/skip.bin', b'skip')
                for entry in entries:
                    archive.writestr(entry.name, entry.data)
            compare(f'zip {mode} 32x{size} read', partial(zip_direct, path, 'data/'),
                    partial(read_zip, path, prefix='data/'), equality(entries), 32 * size,
                    scale=32 if size == 256 else 128)
    limits = (('max_members', 1024), ('max_member_size', 16 * 1024**2), ('max_total_size', 64 * 1024**2))
    compare('zip setup picop exact integer check', partial(zip_limit_setup, limits, picop=False),
            partial(zip_limit_setup, limits, picop=True), equality(None), 1)
    for name, value in (('boolean', True), ('negative', -1), ('subclass', type('Int', (int,), {})(1))):
        for arm in (False, True):
            try:
                zip_limit_setup(((name, value),), picop=arm)
            except (ValueError if name == 'negative' else TypeError):
                pass
            else:
                raise AssertionError('limit predicate accepted invalid value')
    for count in (1, 32):
        subset = entries[:count]
        names = [entry.name for entry in subset]
        compare(f'zip {count} members picop containers',
                partial(zip_container_ops, names, subset, picop=False),
                partial(zip_container_ops, names, subset, picop=True),
                equality((subset, count * len(subset[0].data))), count)


def main():
    print(f'{sys.version.split()[0]} {platform.system()} {platform.machine()}; '
          f'GIL enabled: {sys._is_gil_enabled()}; warm cache; control/trial; 7 alternating pairs')
    print(', '.join(f'{package} {version(package)}' for package in ('picoke', 'picop', 'tomli-w', 'PyYAML')))
    with TemporaryDirectory() as temp:
        root = Path(temp)
        check_batch(root)
        check_lines(root)
        for n in (32, 4096):
            for word, charset in (('snow', 'ASCII'), ('雪 café', 'UTF-8')):
                profile(root, n, word, charset)
        archives(root)


if __name__ == '__main__':
    main()
