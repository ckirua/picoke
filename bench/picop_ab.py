"""Compare picop substitutions with equivalent Cython at the current crypto call sites.

Run with each target interpreter, with Cython, setuptools and picop installed:
    .venv315/bin/python bench/picop_ab.py

Both arms compile into the same scratch extension using the package's compiler
settings. No production source is changed. Repeat runs before applying the
consumer skill's <=0.95 mean ratio and variation/regression gates.
"""

import argparse
from importlib.machinery import EXTENSION_SUFFIXES
from importlib.metadata import version
from importlib.util import module_from_spec, spec_from_file_location
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import sysconfig
import tempfile
import time


_SOURCE = Path(__file__).resolve().parents[1] / "src" / "picoke" / "crypto"
_MODULE = "_picop_ab_native"
_HEADER = """\
import re
from libc.stdint cimport uint64_t
from cpython.bytes cimport PyBytes_CheckExact, PyBytes_FromStringAndSize, PyBytes_GET_SIZE
from picop.cybytes cimport bytes_check_exact, bytes_len, bytes_from_string_and_size
from picop.cylist cimport list_append
from picop.cyset cimport set_add
"""
_SETUP = """\
from importlib.util import find_spec
from pathlib import Path
from Cython.Build import cythonize
from setuptools import Extension, setup

picop = Path(find_spec("picop").origin).parent
setup(ext_modules=cythonize(
    [Extension("_picop_ab_native", ["_picop_ab_native.pyx"], include_dirs=[str(picop)])],
    include_path=[str(picop.parent)],
    compiler_directives={"language_level": 3, "annotation_typing": False},
))
"""


def _function(source, name):
    lines = source.splitlines()
    start = next(i for i, line in enumerate(lines)
                 if line.startswith(("def ", "cdef ")) and f" {name}(" in line)
    end = next((i for i in range(start + 1, len(lines))
                if lines[i] and not lines[i][0].isspace()), len(lines))
    return "\n".join(lines[start:end]).rstrip()


def _replace(source, old, new):
    if source.count(old) != 1:
        raise ValueError(f"benchmark call site changed: expected one {old!r}")
    return source.replace(old, new)


def _variant(source, function, variant, replacements=()):
    head, body = source.split("\n", 1)
    head = _replace(head, f" {function}(", f" {function}_{variant}(")
    for old, new in replacements:
        body = _replace(body, old, new)
    return f"{head}\n{body}\n"


def _native_batches(helper, variant, result_type):
    suffix = f"{helper}_{variant}"
    return f"""\
def value{suffix}(object value):
    return {suffix}(value)

def batch{suffix}(tuple inputs, Py_ssize_t rounds):
    cdef Py_ssize_t i, result = 0
    cdef object value
    cdef {result_type} item
    for i in range(rounds):
        for value in inputs:
            item = {suffix}(value)
            result += {'len(item)' if result_type == 'bytes' else 'item'}
    return result
"""


def _extension_source():
    source = {path.stem: path.read_text(encoding="utf-8") for path in _SOURCE.glob("*.pyx")}
    check = ("PyBytes_CheckExact(", "bytes_check_exact(")
    length = ("PyBytes_GET_SIZE(", "bytes_len(")
    set_mutation = ("found.add(current)", "set_add(found, current)")
    list_mutation = ("pending.append(_base_type(field[\"type\"]))",
                     "list_append(pending, _base_type(field[\"type\"]))")
    compact = ("PyBytes_FromStringAndSize(<const char *>encoded, width)",
               "bytes_from_string_and_size(<const char *>encoded, width)")

    eip712 = source["eip712"]
    dependency = _function(eip712, "_type_dependencies")
    byte_length = _function(source["ed25519"], "_bytes_len")
    if byte_length != _function(source["secp256k1"], "_bytes_len"):
        raise ValueError("signer byte-length helpers diverged; benchmark each separately")
    compact_size = _function(source["bip137"], "_compact_size")
    compiled = [
        _HEADER,
        *(line for line in eip712.splitlines() if line.startswith(("_ARRAY_SUFFIX =", "_PRIMITIVE_TYPE ="))),
        _function(eip712, "_base_type"),
    ]
    for code, name, picop_replacements in (
        (dependency, "_type_dependencies", (list_mutation, set_mutation)),
        (byte_length, "_bytes_len", (check, length)),
        (compact_size, "_compact_size", (compact,)),
    ):
        for variant, replacements in (("direct", ()), ("picop", picop_replacements)):
            compiled.append(_variant(code, name, variant, replacements))
            if name == "_bytes_len":
                compiled.append(_native_batches(name, variant, "Py_ssize_t"))
            if name == "_compact_size":
                compiled.append(_native_batches(name, variant, "bytes"))
    return "\n\n".join(compiled) + "\n"


def _build(directory):
    (directory / f"{_MODULE}.pyx").write_text(_extension_source(), encoding="utf-8")
    (directory / "setup.py").write_text(_SETUP, encoding="utf-8")
    subprocess.run([sys.executable, "setup.py", "build_ext", "--inplace"], cwd=directory, check=True)
    origin = next(path for path in directory.glob(f"{_MODULE}.*")
                  if path.name.endswith(tuple(EXTENSION_SUFFIXES)))
    spec = spec_from_file_location(_MODULE, origin)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _cases(module, iterations):
    class ByteSubtype(bytes):
        def __len__(self):
            return 7

    signature_fields = (b"", bytes(32), bytes(64), bytes(32))
    for variant in ("picop", "direct"):
        validate = getattr(module, f"value_bytes_len_{variant}")
        assert [validate(data) for data in (*signature_fields, ByteSubtype(b"x"))] == [0, 32, 64, 32, 7]
    for label, inputs in (
        ("exact", signature_fields), ("incl. subtype", (*signature_fields, ByteSubtype(b"x"))),
    ):
        yield (f"Signer bytes length ({label})",
               (module.batch_bytes_len_picop, module.batch_bytes_len_direct),
               (inputs, iterations), 1, len(inputs) * iterations)

    sizes = (0, 12, 252, 253, 65535, 65536, 2**32 - 1, 2**32)
    prefixes = tuple(bytes.fromhex(text) for text in (
        "00", "0c", "fc", "fdfd00", "fdffff", "fe00000100", "feffffffff", "ff0000000001000000",
    ))
    sizes, prefixes = zip(*((size, prefix) for size, prefix in zip(sizes, prefixes) if size <= sys.maxsize))
    for variant in ("picop", "direct"):
        validate = getattr(module, f"value_compact_size_{variant}")
        assert tuple(map(validate, sizes)) == prefixes
    for label, inputs in (("small", sizes[:3]), ("width boundaries", sizes[2:])):
        yield (f"BIP-137 CompactSize ({label})", (module.batch_compact_size_picop, module.batch_compact_size_direct),
               (inputs, iterations), 1, len(inputs) * iterations)


    for label, types, primary in (
        ("Agent", {"Agent": [{"type": "string"}, {"type": "bytes32"}]}, "Agent"),
        ("nested", {"Item": [{"type": "address"}, {"type": "uint256"}],
                    "Batch": [{"type": "Item[][2]"}, {"type": "Item"}, {"type": "string"}]}, "Batch"),
    ):
        assert module._type_dependencies_picop(primary, types) == module._type_dependencies_direct(primary, types) == set(types)
        yield (f"EIP-712 dependency closure ({label})",
               (module._type_dependencies_picop, module._type_dependencies_direct),
               (primary, types), iterations, iterations)


def _measure(function, arguments, repeats, operations):
    start = time.perf_counter_ns()
    for _ in range(repeats):
        result = function(*arguments)
    return (time.perf_counter_ns() - start) / operations, result


def _compare(case, samples, warmups):
    name, arms, arguments, repeats, operations = case
    expected = arms[0](*arguments)
    if arms[1](*arguments) != expected:
        raise ValueError(f"A/B mismatch in {name}")
    durations = [[], []]
    for sample in range(warmups + samples):
        order = (0, 1) if sample % 2 == 0 else (1, 0)
        for arm in order:
            duration, result = _measure(arms[arm], arguments, repeats, operations)
            if result != expected:
                raise ValueError(f"timed result mismatch in {name}")
            if sample >= warmups:
                durations[arm].append(duration)
    means = [statistics.mean(values) for values in durations]
    spreads = [statistics.stdev(values) for values in durations]
    ratios = [p / d for p, d in zip(*durations)]
    print(f"{name}: picop {means[0]:.2f} ± {spreads[0]:.2f}, direct {means[1]:.2f} ± {spreads[1]:.2f} "
          f"ns/op; mean ratio {means[0] / means[1]:.3f}x; paired ratios "
          f"{', '.join(f'{ratio:.3f}' for ratio in ratios)}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=9)
    parser.add_argument("--iterations", type=int, default=10_000)
    parser.add_argument("--warmups", type=int, default=2)
    args = parser.parse_args()
    if args.samples < 5 or args.warmups < 1 or args.iterations < 1:
        parser.error("use at least five samples, one warmup, and one iteration")
    print(f"{sys.version.splitlines()[0]} | {platform.platform()} | CPU {platform.processor() or platform.machine()} | "
          f"GIL enabled={sys._is_gil_enabled()}", flush=True)
    print(f"Cython {version('Cython')} | picop {version('picop')} | setuptools {version('setuptools')} | "
          f"CC={os.environ.get('CC') or sysconfig.get_config_var('CC')} | "
          f"CFLAGS={os.environ.get('CFLAGS') or sysconfig.get_config_var('CFLAGS')}", flush=True)
    print(f"{args.samples} paired samples, {args.warmups} warmup pairs, {args.iterations} iterations/block", flush=True)
    with tempfile.TemporaryDirectory(prefix="picoke-picop-ab-") as directory:
        module = _build(Path(directory))
        for case in _cases(module, args.iterations):
            _compare(case, args.samples, args.warmups)


if __name__ == "__main__":
    main()
