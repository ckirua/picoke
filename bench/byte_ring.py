"""Time bounded byte FIFOs under the same workload; write raw samples as JSON."""

import argparse
import gc
import importlib
import json
import os
import platform
import statistics
import sys
import threading
import time
import tracemalloc
from importlib.metadata import version


def _construct(factory, capacity, size, turns):
    for _ in range(turns):
        factory(capacity, size)


def _round_trip(ring, item, turns):
    for _ in range(turns):
        ring.try_push(item)
        ring.try_pop()


def _bursts(ring, items, turns):
    for _ in range(turns):
        for item in items:
            ring.try_push(item)
        for _ in items:
            ring.try_pop()


def _push_full(ring, item, turns):
    for _ in range(turns):
        ring.try_push(item)


def _pop_empty(ring, turns):
    for _ in range(turns):
        ring.try_pop()


def _threaded(factory, items, capacity, size):
    ring = factory(capacity, size)
    ready = threading.Barrier(2, timeout=10)
    errors = []
    stop = threading.Event()
    clock = time.perf_counter_ns

    def produce():
        try:
            ready.wait()
            for item in items:
                while not ring.try_push(item):
                    if stop.is_set():
                        return
                    time.sleep(0)
        except BaseException as exc:
            errors.append(exc)
            stop.set()

    producer = threading.Thread(target=produce, daemon=True)
    producer.start()
    try:
        end = time.monotonic() + 10
        start = clock()
        ready.wait()
        for expected in items:
            item = ring.try_pop()
            while item is None:
                if errors or time.monotonic() >= end:
                    raise RuntimeError('threaded FIFO did not make progress') from next(iter(errors), None)
                time.sleep(0)
                item = ring.try_pop()
            if item != expected:
                raise RuntimeError('threaded FIFO returned out-of-order data')
        elapsed = clock() - start
    finally:
        stop.set()
        producer.join(10)
    if errors or producer.is_alive() or ring.try_pop() is not None:
        raise RuntimeError('threaded FIFO did not finish cleanly') from next(iter(errors), None)
    return elapsed / len(items)


def _preflight(factory, items, capacity, size):
    ring = factory(capacity, size)
    for at in range(2):
        assert ring.try_pop() is None
        if at:
            assert ring.try_push(b'') is True and ring.try_pop() == b''
        for item in items:
            assert ring.try_push(item) is True
        assert ring.try_push(b'extra') is False
        assert tuple(ring.try_pop() for _ in items) == items
    assert ring.try_pop() is None


def _memory(factory, items, capacity, size):
    # Payloads stay alive outside the queue; measure additional traced storage.
    gc.collect()
    tracemalloc.start()
    try:
        before = tracemalloc.get_traced_memory()[0]
        ring = factory(capacity, size)
        empty = tracemalloc.get_traced_memory()[0] - before
        for item in items:
            ring.try_push(item)
        full = tracemalloc.get_traced_memory()[0] - before
    finally:
        tracemalloc.stop()
    return {'empty_bytes': empty, 'full_bytes': full}


def _sample(factory, items, capacity, size, turns, threaded_items):
    clock = time.perf_counter_ns
    item = items[-1]
    ring = factory(capacity, size)
    full = factory(capacity, size)
    for queued in items:
        full.try_push(queued)
    batches = items[:64]
    burst_turns = max(1, turns // len(batches))
    build_turns = max(1, turns // 32)
    values = {}
    for name, fn, args, ops in (
        ('construct_destroy', _construct, (factory, capacity, size, build_turns), build_turns),
        ('round_trip', _round_trip, (ring, item, turns), turns),
        ('burst', _bursts, (ring, batches, burst_turns), burst_turns * len(batches)),
        ('push_full', _push_full, (full, item, turns), turns),
        ('pop_empty', _pop_empty, (ring, turns), turns),
    ):
        start = clock()
        fn(*args)
        values[name] = (clock() - start) / ops
    if threaded_items:
        values['two_threads'] = _threaded(factory, threaded_items, capacity, size)
    return values


def _backend(spec):
    label, module, symbol = spec.split(':')
    return label, getattr(importlib.import_module(module), symbol)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend', action='append', metavar='LABEL:MODULE:CLASS',
                        help='override the default deque and installed ByteRing backends')
    parser.add_argument('--module-dir', action='append', default=[],
                        help='additional import directory for experimental backends')
    parser.add_argument('--samples', type=int, default=9)
    parser.add_argument('--turns', type=int, default=100_000)
    parser.add_argument('--threaded-items', type=int, default=32_768)
    parser.add_argument('--capacity', type=int, default=1024)
    parser.add_argument('--sizes', type=int, nargs='+', default=(32, 4096))
    args = parser.parse_args()
    if args.samples < 5 or args.turns < 1 or args.capacity < 64 or min(args.sizes) < 5 or args.threaded_items < 0:
        parser.error('require five samples, positive turns, capacity >= 64, sizes >= 5, and nonnegative threaded-items')
    sys.path[:0] = args.module_dir
    arms = dict(_backend(spec) for spec in (args.backend or (
        'deque:_byte_ring_deque:ByteRing', 'picoke:picoke.queues:ByteRing',
    )))
    report = {
        'python': sys.version, 'gil': sys._is_gil_enabled(), 'platform': platform.platform(),
        'cpu_affinity': sorted(os.sched_getaffinity(0)) if hasattr(os, 'sched_getaffinity') else None,
        'picop': version('picop'), 'cython': version('Cython'), 'arguments': vars(args), 'sizes': {},
    }
    for size in args.sizes:
        samples = {label: [] for label in arms}
        padding = bytes(size - 4)
        items = tuple(i.to_bytes(4, 'little') + padding for i in range(args.capacity))
        for factory in arms.values():
            _preflight(factory, items, args.capacity, size)
        threaded = (items * (args.threaded_items // len(items) + 1))[:args.threaded_items]
        memory = {}
        for label, factory in arms.items():
            memory[label] = _memory(factory, items, args.capacity, size)
            _sample(factory, items, args.capacity, size, args.turns // 10, ())
        labels = tuple(arms)
        for sample in range(args.samples):
            order = labels[sample % len(labels):] + labels[:sample % len(labels)]
            if (sample // len(labels)) % 2:
                order = order[::-1]
            for label in order:
                samples[label].append(_sample(arms[label], items, args.capacity, size, args.turns, threaded))
        report['sizes'][size] = {
            'samples_ns': samples, 'queue_storage': memory,
            'median_ns': {label: {name: statistics.median(row[name] for row in runs) for name in runs[0]}
                          for label, runs in samples.items()},
        }
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
