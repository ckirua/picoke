"""Interleaved same-process baseline/candidate codec comparison.

Run in an environment with the baseline installed:
    python bench/binary_logging_opt.py CANDIDATE.py short read
Use ``aa`` instead of a candidate path for the same-code control. Shapes:
short, nested, unicode, large, numbers, floats, wide. Modes: encode, read,
shortread. Each sample alternates 30 blocks per arm and records thread CPU
and wall-clock ratios.
"""

import argparse
import importlib.util
import io
import json
import logging
import platform
import statistics
import sys
import time

import picoke.binary_logging as base

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("candidate", help="candidate source file, or aa for same-code control")
parser.add_argument("shape", choices=("short", "nested", "unicode", "large", "numbers", "floats", "wide"))
parser.add_argument("mode", choices=("encode", "read", "shortread"))
args = parser.parse_args()
path, shape, mode = args.candidate, args.shape, args.mode
if shape == "wide" and mode == "encode":
    parser.error("the wide-record fixture only supports read modes")
if path == 'aa':
    candidate = base
else:
    spec = importlib.util.spec_from_file_location('picoke._comparison_candidate', path)
    candidate = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = candidate
    spec.loader.exec_module(candidate)
fields = {'short': {'city': 'Oslo', 'n': 7}, 'nested': {'user': {'id': 42, 'tags': ['a', {'active': True}]}, 'items': [1, 2, 3]}, 'unicode': {'city': '雪', 'greeting': 'こんにちは', 'nested': {'emoji': '☃', 'value': ['café', 'λ']}}, 'large': {'payload': 'x' * 32768}, 'numbers': {'values': list(range(128))}, 'floats': {'values': [i / 7 for i in range(128)]}, 'wide': {}}[shape]
r = logging.LogRecord('bench', logging.INFO, '', 1, 'message', (), None)
r.created = 1700000000.125
r.fields = fields
fmt = logging.Formatter()
frame = base._encode_record(r, fmt)
if shape == "wide":
    import msgpack
    import struct
    import zlib

    payload = msgpack.packb({f"field_{i}": i for i in range(128)}, use_bin_type=True)
    frame = b"PKL\x01" + struct.pack(">I", len(payload)) + payload + struct.pack(">I", zlib.crc32(payload))
else:
    assert frame == candidate._encode_record(r, fmt)
assert list(base.iter_binary_records(io.BytesIO(frame))) == list(candidate.iter_binary_records(io.BytesIO(frame)))
class ShortRead:
    def __init__(self, data): self.stream = io.BytesIO(data)
    def read(self, size): return self.stream.read(min(7, size))

def run(module, count):
    if mode == 'encode':
        for _ in range(count): out = module._encode_record(r, fmt)
    elif mode == 'read':
        for _ in range(count): out = list(module.iter_binary_records(io.BytesIO(frame)))
    else:
        for _ in range(count): out = list(module.iter_binary_records(ShortRead(frame)))
    return out

count = 2 if shape == 'large' and mode == 'shortread' else 20
blocks = 30
samples = []
for k in range(9):
    cpu = [0, 0]; wall = [0, 0]
    for b in range(blocks):
        for idx in ((0, 1) if (b+k)%2 == 0 else (1, 0)):
            mod = (base, candidate)[idx]
            w = time.perf_counter_ns(); t = time.thread_time_ns(); run(mod, count)
            cpu[idx] += time.thread_time_ns() - t; wall[idx] += time.perf_counter_ns() - w
    samples.append({'base_cpu':cpu[0], 'candidate_cpu':cpu[1], 'base_wall':wall[0], 'candidate_wall':wall[1], 'cpu_ratio':cpu[1]/cpu[0], 'wall_ratio':wall[1]/wall[0]})
print(json.dumps({'python':sys.version,'platform':platform.platform(),'candidate':path,'shape':shape,'mode':mode,'count':count,'blocks':blocks,'samples':samples,'cpu_ratio_median':statistics.median(x['cpu_ratio'] for x in samples),'wall_ratio_median':statistics.median(x['wall_ratio'] for x in samples)}))
