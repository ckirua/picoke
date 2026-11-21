# File I/O plan

Use `picoke.io.files` for path-based format and line helpers. Keep in-memory
codecs, compression, and network transports out of this package's public API.
Start with conventional Python and existing format providers; do not expose
backend selection or duplicate `fast` entry points.

## Public API

Import these helpers from `picoke.io.files`, unless noted. Paths can be strings
or `PathLike[str]`. Ordinary I/O errors propagate. All writers overwrite;
none promise atomicity or durability.

| API | Contract |
|---|---|
| `read_json(path)`, `write_json(path, value)` | Strict UTF-8. Reads one document, rejects `NaN`, `Infinity`, and `-Infinity`, and otherwise uses stdlib number/duplicate-key behavior. Syntax errors remain `json.JSONDecodeError`. Output is compact, ASCII-escaped JSON with one LF. Serialization failures leave the old destination intact. |
| `iter_lines(path, *, encoding='utf-8', strip=False, skip_empty=False, comment_prefix=None)` | Opens lazily, decodes strictly, translates CRLF and bare CR, and removes one line terminator. Then optionally strips, skips empty strings, and filters the literal prefix in that order. Without stripping, prefix matching starts at column one. An empty prefix is invalid. The final line need not have a newline. Use `list(iter_lines(...))` for a snapshot; no implicit cache or mmap. Close explicitly when stopping early. |
| `read_toml(path)`, `write_toml(path, value)` | Uses stdlib `tomllib` to read. Writing a mapping needs `picoke[toml]`, serializes via `tomli-w` before opening, and preserves the provider's UTF-8 text. |
| `read_yaml(path)`, `write_yaml(path, value)` | Needs `picoke[yaml]`. Reads one strict UTF-8 document via `yaml.safe_load`; empty input returns `None`. `safe_dump` writes Unicode, keeps mapping insertion order, and serializes before opening. Arbitrary Python tags are rejected; this is not a resource-exhaustion guarantee. |
| `read_jsonl(path)`, `write_jsonl(path, records)` | See the streaming contract below. |
| `picoke.io.files.zip.read_zip(path, *, names=None, prefix=None, max_members=1024, max_member_size=16 * 1024**2, max_total_size=64 * 1024**2)` | Returns a list of frozen `ZipEntry(name, data)` objects. Details below. |

Install `picoke[files]` to enable both optional providers. They load only
when called; a missing provider raises `ImportError` naming the extra.

## JSON Lines contract

- UTF-8 only; LF and CRLF delimit records, and the last record need not have a newline. Empty files have no records. Blank lines, invalid UTF-8, and invalid JSON raise `JSONLDecodeError`, with the one-based physical line number and the original failure as cause. Numeric `NaN` and `Infinity` literals are not JSON values.
- The reader opens when advanced and closes on exhaustion, error, or explicit generator close. Use `contextlib.closing` when stopping early. Memory scales with the largest record, not the number of records; this is not a maximum record-size policy.
- The writer truncates the path and consumes the iterable once. It emits compact JSON followed by LF, rejects non-finite floats, and does not promise atomicity or durability. Serialization or input-iteration failure can leave prior complete lines; an I/O failure can leave a partial last line.
- Empty input still truncates the destination; the writer creates its encoder only when it receives the first record.

## ZIP member reads

- `names` and `prefix` are literal case-sensitive filters that intersect.
  Directories are omitted; selected entries retain archive order. Duplicate
  selected names raise `ValueError`; no matches returns an empty list.
- The limits count selected files and their uncompressed bytes. Set them
  explicitly for the expected workload; they must be nonnegative built-in
  integers, excluding `bool`. Advertised sizes are checked before payload I/O,
  and returned payload bytes are checked as each member is read through EOF.
  Archive errors, including CRC failures, propagate.
- ZIP names are labels, not safe destination paths. The API never extracts
  members to disk. Bounds do not cover the central-directory index or CPU
  spent parsing/decompressing an archive.

## Verification and optimization

The installed-wheel matrix uses CPython 3.14 and 3.15 on Linux. The
[file benchmarks](../bench/README.md#file-utilities) cover setup, warm-cache
throughput, memory, and candidate substitutions. Use same-provider controls
and test behavior before adopting picop, batching, Cython, or mmap.
The [calibrated optimization pass](../bench/README.md#calibrated-file-optimization-pass)
compared six same-contract candidates on both ABIs. None passed the
calibration, performance, and memory gates together; the file implementations
and provider choices are unchanged.
The [JSONL follow-up](../bench/README.md#jsonl-defer-encoder-creation-until-the-first-record)
retained lazy per-write encoder reuse after measuring the empty-input guard on both ABIs.

Fresh base-wheel installs on Linux x86-64 accepted TOML reads and reported
the named optional extra before touching a path for TOML writes and YAML.
Installing `[files]` then passed the 519-test suite on CPython 3.14.7 and
3.15.0rc2. No free-threaded or cross-platform claim is made.
