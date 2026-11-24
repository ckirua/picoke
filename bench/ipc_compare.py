"""Compare importable SharedByteRing implementations with paired SPSC traffic.

Use the same module for both options for an A/A control; candidate modules
must implement the same public API and shared-memory semantics.
"""
import argparse
import importlib
import json
import multiprocessing as mp
import os
import platform
import statistics
import sys
import time


def producer(modules, names, pairs, blocks, count, size, gate, ack, output):
    rings = [importlib.import_module(module).SharedByteRing.attach(name) for module, name in zip(modules, names)]
    payload = b'x' * size
    gate.wait()
    samples = []
    try:
        for pair in range(pairs):
            totals = [0, 0]
            for block in range(blocks):
                for idx in ((0, 1) if (pair + block) % 2 == 0 else (1, 0)):
                    started = time.perf_counter_ns()
                    for seq in range(count):
                        item = seq.to_bytes(8, 'little') + payload
                        while not rings[idx].try_push(item):
                            pass
                    if ack.recv() != idx:
                        raise RuntimeError('consumer order mismatch')
                    totals[idx] += time.perf_counter_ns() - started
            samples.append({'baseline_ns': totals[0], 'candidate_ns': totals[1], 'ratio': totals[1] / totals[0]})
        output.put(samples)
    finally:
        for ring in rings: ring.close()


def consumer(modules, names, pairs, blocks, count, size, gate, ack):
    rings = [importlib.import_module(module).SharedByteRing.attach(name) for module, name in zip(modules, names)]
    expected = b'x' * size
    gate.wait()
    try:
        for pair in range(pairs):
            for block in range(blocks):
                for idx in ((0, 1) if (pair + block) % 2 == 0 else (1, 0)):
                    for seq in range(count):
                        item = rings[idx].try_pop()
                        while item is None:
                            item = rings[idx].try_pop()
                        if item[:8] != seq.to_bytes(8, 'little') or item[8:] != expected:
                            raise AssertionError((pair, block, idx, seq))
                    ack.send(idx)
    finally:
        for ring in rings: ring.close()


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--baseline', required=True); p.add_argument('--candidate', required=True)
    p.add_argument('--size', type=int, default=128); p.add_argument('--capacity', type=int, default=64)
    p.add_argument('--count', type=int, default=200); p.add_argument('--blocks', type=int, default=10)
    p.add_argument('--pairs', type=int, default=9)
    a = p.parse_args(); modules = (a.baseline, a.candidate)
    ctx = mp.get_context('spawn')
    rings = [importlib.import_module(module).SharedByteRing.create(a.capacity, a.size + 8) for module in modules]
    gate = ctx.Event(); recv, send = ctx.Pipe(False); out = ctx.Queue()
    args = (modules, [r.name for r in rings], a.pairs, a.blocks, a.count, a.size, gate)
    ps = [ctx.Process(target=consumer, args=(*args, send)), ctx.Process(target=producer, args=(*args, recv, out))]
    try:
        for proc in ps: proc.start()
        gate.set()
        samples = out.get(timeout=120)
        for proc in ps: proc.join(10)
        if any(proc.exitcode != 0 for proc in ps): raise RuntimeError('worker failure')
        print(json.dumps({'python':sys.version,'platform':platform.platform(),'args':vars(a),'samples':samples,'median_ratio':statistics.median(x['ratio'] for x in samples)}, indent=2))
    finally:
        for proc in ps:
            if proc.is_alive(): proc.terminate(); proc.join(5)
        recv.close(); send.close(); out.close(); out.join_thread()
        for ring in rings: ring.close(); ring.unlink()

if __name__ == '__main__': main()
