#!/usr/bin/env python3
"""End-to-end bounded background binary writer benchmark.

Run from the repository root with its package installed/importable, e.g.
``python bench/background_writer.py --processes 3 --pairs 9``.  The default
matrix measures one/four producers, 128 B/4 KiB immutable payloads, idle and
controlled slow sinks, and blocking/nonblocking admission. Each arm runs in a
fresh child process; JSON results include exact output parity, producer and
end-to-end latency, queue outcomes, tracemalloc peak, and process max RSS.
Optional native implementations can be named with ``--candidate module:Class``.
They must implement BackgroundWriter's constructor and methods. Results are
measurements, not a claim of durability or downstream zero-copy behavior.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import queue
import math
import statistics
import subprocess
import sys
import threading
import time
import tracemalloc
from typing import Any, Callable


def _payload(size: int) -> bytes:
    seed = b'background-writer-benchmark:'
    return (seed * ((size + len(seed) - 1) // len(seed)))[:size]


class _Sink:
    def __init__(self, delay: float) -> None:
        self.data = bytearray()
        self.delay = delay
        self.lock = threading.Lock()

    def write(self, value: Any) -> int:
        if self.delay:
            time.sleep(self.delay)
        raw = bytes(value)
        with self.lock:
            self.data.extend(raw)
        return len(raw)

    def flush(self) -> None:
        pass


def _write_all(sink: _Sink, data: bytes) -> None:
    offset = 0
    current: Any = data
    while offset < len(data):
        written = sink.write(current)
        remaining = len(data) - offset
        if type(written) is not int or written <= 0 or written > remaining:
            raise OSError('invalid binary stream write result')
        offset += written
        current = memoryview(data)[offset:]


class _TextSink:
    def __init__(self, sink: _Sink) -> None:
        self.sink = sink

    def write(self, text: str) -> int:
        data = text.encode('utf-8')
        self.sink.write(data)
        return len(text)

    def flush(self) -> None:
        self.sink.flush()


def _handler_one(arm: str, producers: int, count: int, capacity: int,
                 block: bool, delay: float) -> dict[str, Any]:
    import logging
    import logging.handlers
    from picoke.logging import BackgroundJSONHandler, BackgroundWriter, JSONFormatter

    sink = _Sink(delay)
    text_sink = _TextSink(sink)
    formatter = JSONFormatter()
    errors: list[BaseException] = []
    if arm == 'handler-sync':
        handler: Any = logging.StreamHandler(text_sink)
        handler.setFormatter(formatter)
        finish = handler.flush
    elif arm == 'queue-handler-listener':
        target = logging.StreamHandler(text_sink)
        target.setFormatter(formatter)
        handler = logging.handlers.QueueHandler(queue.Queue())
        listener = logging.handlers.QueueListener(handler.queue, target)
        listener.start()
        finish = listener.stop
    else:
        writer = BackgroundWriter(sink, capacity=capacity, max_item_size=1048576)
        handler = BackgroundJSONHandler(writer)
        finish = writer.close
    original_factory = logging.getLogRecordFactory()
    def fixed_factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = original_factory(*args, **kwargs)
        record.created = 1_700_000_000.125
        return record
    logging.setLogRecordFactory(fixed_factory)
    logger = logging.Logger('background_writer_benchmark', logging.INFO)
    logger.propagate = False
    logger.addHandler(handler)
    expected_lines = []
    for index in range(producers):
        for n in range(count):
            record = logging.LogRecord('background_writer_benchmark', logging.INFO,
                'bench', 1, 'event', (), None)
            record.created = 1_700_000_000.125
            record.fields = {'producer': index, 'sequence': n}
            expected_lines.append((formatter.format(record) + '\n').encode('utf-8'))
    accepted = [0] * producers
    started = time.perf_counter_ns()
    def emit(index: int) -> None:
        for n in range(count):
            try:
                logger.info('event', extra={'fields': {'producer': index, 'sequence': n}})
                accepted[index] += 1
            except BaseException as exc:
                errors.append(exc)
    threads = [threading.Thread(target=emit, args=(i,)) for i in range(producers)]
    for t in threads: t.start()
    for t in threads: t.join()
    finish()
    elapsed = time.perf_counter_ns() - started
    logging.setLogRecordFactory(original_factory)
    logger.removeHandler(handler)
    if errors: raise errors[0]
    actual = bytes(sink.data)
    lines = actual.splitlines(keepends=True)
    if sorted(lines) != sorted(expected_lines):
        raise AssertionError(f'{arm}: exact JSON output mismatch')
    return {'arm': arm, 'workload': 'formatter+handler', 'producers': producers,
            'accepted': sum(accepted), 'rejected': 0, 'drain_close_wall_ns': elapsed,
            'throughput_items_s': sum(accepted) / (elapsed / 1e9),
            'output_bytes': len(actual), 'valid_utf8_json_lines': len(lines),
            'byte_parity': True,
            'queue_handler_listener_note': 'unbounded QueueHandler/QueueListener; different full/shutdown semantics'}


def _factory(name: str, capacity: int, max_size: int) -> tuple[Any, str]:
    if name == 'picoke':
        from picoke.logging import BackgroundWriter
        return BackgroundWriter, 'picoke.logging.BackgroundWriter'
    if name == 'queue':
        return None, 'stdlib queue.Queue + writer thread'
    if name == 'sync':
        return None, 'synchronous binary writes'
    module, cls = name.split(':', 1)
    return getattr(importlib.import_module(module), cls), name


def _one(arm: str, producers: int, size: int, count: int, capacity: int,
         block: bool, delay: float, measure_memory: bool = False) -> dict[str, Any]:
    payload = _payload(size)
    accepted = [0] * producers
    rejected = [0] * producers
    producer_ns = [0] * producers
    sink = _Sink(delay)
    expected = payload * (count * producers)
    cls, _ = _factory(arm, capacity, size)
    if measure_memory:
        tracemalloc.start()
    start = time.perf_counter_ns()
    shutdown_ns = 0
    if arm == 'sync':
        def producer(index: int) -> None:
            t = time.perf_counter_ns()
            for _ in range(count):
                _write_all(sink, payload)
                accepted[index] += 1
            producer_ns[index] = time.perf_counter_ns() - t
        threads = [threading.Thread(target=producer, args=(i,)) for i in range(producers)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        shutdown_start = time.perf_counter_ns()
        sink.flush()
        shutdown_ns = time.perf_counter_ns() - shutdown_start
    elif arm == 'queue':
        q: queue.Queue[bytes | None] = queue.Queue(capacity)
        errors: list[BaseException] = []
        def consume() -> None:
            try:
                while True:
                    item = q.get()
                    try:
                        if item is None: return
                        _write_all(sink, item)
                    finally:
                        q.task_done()
            except BaseException as exc:
                errors.append(exc)
        worker = threading.Thread(target=consume, daemon=True)
        worker.start()
        def producer(index: int) -> None:
            t = time.perf_counter_ns()
            for _ in range(count):
                try:
                    if block:
                        q.put(payload); accepted[index] += 1
                    else:
                        q.put_nowait(payload); accepted[index] += 1
                except queue.Full:
                    rejected[index] += 1
            producer_ns[index] = time.perf_counter_ns() - t
        threads = [threading.Thread(target=producer, args=(i,)) for i in range(producers)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        shutdown_start = time.perf_counter_ns()
        q.join()
        sink.flush()
        q.put(None)
        worker.join()
        shutdown_ns = time.perf_counter_ns() - shutdown_start
        if errors: raise errors[0]
        expected = payload * sum(accepted)
    else:
        writer = cls(sink, capacity=capacity, max_item_size=size)
        errors = []
        def producer(index: int) -> None:
            t = time.perf_counter_ns()
            for _ in range(count):
                try:
                    ok = writer.write(payload, block=block)
                    if ok: accepted[index] += 1
                    else: rejected[index] += 1
                except BaseException as exc:
                    errors.append(exc); break
            producer_ns[index] = time.perf_counter_ns() - t
        threads = [threading.Thread(target=producer, args=(i,)) for i in range(producers)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        shutdown_start = time.perf_counter_ns()
        writer.flush()
        writer.close()
        shutdown_ns = time.perf_counter_ns() - shutdown_start
        if errors: raise errors[0]
        expected = payload * sum(accepted)
    elapsed = time.perf_counter_ns() - start
    peak = None
    if measure_memory:
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
    actual = bytes(sink.data)
    if actual != expected:
        raise AssertionError(f'{arm}: output byte mismatch ({len(actual)} != {len(expected)})')
    try:
        import resource
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    except ImportError:
        rss = None
    return {'arm': arm, 'producers': producers, 'size': size, 'items_per_producer': count,
            'block': block, 'delay_seconds': delay, 'accepted': sum(accepted),
            'rejected': sum(rejected), 'producer_ns': producer_ns,
            'producer_wall_ns': max(producer_ns, default=0), 'drain_close_wall_ns': elapsed,
            'shutdown_ns': shutdown_ns,
            'throughput_items_s': sum(accepted) / (elapsed / 1e9), 'tracemalloc_peak_bytes': peak,
            'max_rss_platform_units': rss, 'output_bytes': len(actual), 'byte_parity': True}


def _child(args: list[str]) -> int:
    cfg = json.loads(args[0])
    workload = cfg.pop('workload', 'binary')
    result = (_handler_one(**cfg) if workload == 'handler' else _one(**cfg))
    print(json.dumps(result, separators=(',', ':')))
    return 0


def _fresh(cfg: dict[str, Any], affinity: str | None) -> dict[str, Any]:
    command = [sys.executable, __file__, '--_child', json.dumps(cfg)]
    if affinity and hasattr(os, 'sched_setaffinity'):
        # Affinity is applied by this fresh child before importing the workload.
        command = [sys.executable, '-c',
                   'import os,runpy,sys; os.sched_setaffinity(0,{int(sys.argv[1])}); '
                   'p=sys.argv[2]; sys.argv=[p,"--_child",sys.argv[3]]; runpy.run_path(p,run_name="__main__")',
                   affinity, __file__, json.dumps(cfg)]
    output = subprocess.check_output(command, text=True)
    return json.loads(output.strip().splitlines()[-1])


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    if raw and raw[0] == '--_child':
        return _child(raw[1:])
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--processes', type=int, default=3, help='fresh-process repetitions per arm')
    parser.add_argument('--pairs', type=int, default=9, help='alternating paired blocks')
    parser.add_argument('--items', type=int, default=1000, help='items per producer per block')
    parser.add_argument('--capacity', type=int, default=256)
    parser.add_argument('--slow-delay', type=float, default=0.0001)
    parser.add_argument('--affinity', type=str, help='single CPU number, where supported')
    parser.add_argument('--candidate', action='append', default=[], metavar='MODULE:CLASS')
    parser.add_argument('--workload', choices=('binary', 'handler', 'all'), default='all')
    parser.add_argument('--quick', action='store_true', help='reduce binary matrix to idle sink')
    args = parser.parse_args(raw)
    arms = ['sync', 'queue', 'picoke', *args.candidate]
    dimensions = [(p, size, delay, block) for p in (1, 4) for size in (128, 4096)
                  for delay in ((0.0,) if args.quick else (0.0, args.slow_delay))
                  for block in (True, False)]
    result: dict[str, Any] = {'runtime': sys.version, 'platform': sys.platform,
        'gil_enabled': getattr(sys, '_is_gil_enabled', lambda: True)(),
        'picop': _picop_version(), 'capacity': args.capacity, 'items': args.items,
        'fresh_processes': args.processes, 'requested_paired_blocks': args.pairs,
        'affinity_cpu': args.affinity, 'slow_sink_delay_seconds': args.slow_delay,
        'memory_runs_separate_from_timing': True,
        'arms': arms, 'same_code_control': {}, 'fixtures': {}, 'memory_measurements': {}}
    if args.workload in ('binary', 'all'):
        for producers, size, delay, block in dimensions:
            key = f'p{producers}-b{size}-delay{delay:g}-block{int(block)}'
            control = _fixture_calibrate(producers, size, delay, block, args)
            result['same_code_control'][key] = control
            pairs = control['pairs_used']
            blocks = []
            for pair in range(pairs):
                order = arms if pair % 2 == 0 else list(reversed(arms))
                block_results = {}
                for arm in order:
                    cfg = {'arm': arm, 'producers': producers, 'size': size, 'count': args.items,
                           'capacity': args.capacity, 'block': block, 'delay': delay}
                    block_results[arm] = [_fresh(cfg, args.affinity) for _ in range(args.processes)]
                blocks.append(block_results)
            pair_ratios: dict[str, list[float]] = {}
            shutdown_ratios: dict[str, list[float]] = {}
            for arm in arms:
                if arm == 'picoke': continue
                ratios, shutdown_pair = [], []
                for paired in blocks:
                    left = statistics.median(r['drain_close_wall_ns'] for r in paired[arm])
                    right = statistics.median(r['drain_close_wall_ns'] for r in paired['picoke'])
                    ratios.append(left / right)
                    left_shutdown = statistics.median(r['shutdown_ns'] for r in paired[arm])
                    right_shutdown = statistics.median(r['shutdown_ns'] for r in paired['picoke'])
                    shutdown_pair.append(left_shutdown / right_shutdown if right_shutdown else None)
                pair_ratios[arm] = ratios
                shutdown_ratios[arm] = shutdown_pair
            result['fixtures'][key] = {'samples': blocks, 'paired_ratios_vs_picoke': pair_ratios,
                'median_paired_ratio_vs_picoke': {arm: statistics.median(ratios)
                                                  for arm, ratios in pair_ratios.items()},
                'wins_vs_picoke': {arm: sum(ratio < 1.0 for ratio in ratios)
                                   for arm, ratios in pair_ratios.items()},
                'paired_shutdown_ratios_vs_picoke': shutdown_ratios,
                'successful_path_only': True, 'queue_failure_lifecycle_parity': 'unmeasured'}
            for arm in arms:
                cfg = {'arm': arm, 'producers': producers, 'size': size, 'count': args.items,
                       'capacity': args.capacity, 'block': block, 'delay': delay,
                       'measure_memory': True}
                result['memory_measurements'][f'{key}:{arm}'] = _fresh(cfg, args.affinity)
    if args.workload in ('handler', 'all'):
        handler_arms = ['handler-sync', 'background-json-handler', 'queue-handler-listener']
        fixtures = {}
        for producers in (1, 4):
            armsamples = {arm: [] for arm in handler_arms}
            for pair in range(args.pairs):
                order = handler_arms if pair % 2 == 0 else list(reversed(handler_arms))
                for arm in order:
                    cfg = {'workload': 'handler', 'arm': arm, 'producers': producers,
                           'count': args.items, 'capacity': args.capacity, 'block': True, 'delay': 0.0}
                    armsamples[arm].append([_fresh(cfg, args.affinity) for _ in range(args.processes)])
            medians = {arm: statistics.median(r['drain_close_wall_ns']
                for block in blocks for r in block) for arm, blocks in armsamples.items()}
            fixtures[f'producers-{producers}'] = {'samples': armsamples, 'medians_ns': medians,
                'ratio_vs_background': {arm: medians[arm] / medians['background-json-handler']
                                        for arm in handler_arms if arm != 'background-json-handler'},
                'queue_handler_listener_note': 'separate non-comparable baseline: QueueHandler prepares/copies records, uses unbounded queue, and has different shutdown semantics'}
        result['formatter_handler'] = fixtures
    print(json.dumps(result, ensure_ascii=True, separators=(',', ':')))
    return 0




def _fixture_calibrate(producers: int, size: int, delay: float, block: bool,
                      args: Any) -> dict[str, Any]:
    pairs = args.pairs
    attempts = []
    for retry in (False, True):
        ratios = []
        samples = []
        for _ in range(pairs):
            cfg = {'arm': 'picoke', 'producers': producers, 'size': size,
                   'count': args.items, 'capacity': args.capacity,
                   'block': block, 'delay': delay}
            left, right = _fresh(cfg, args.affinity), _fresh(cfg, args.affinity)
            ratio = left['drain_close_wall_ns'] / right['drain_close_wall_ns']
            ratios.append(ratio)
            samples.append({'left': left, 'right': right, 'ratio': ratio})
        median = statistics.median(ratios)
        required = math.ceil(7 * pairs / 9)
        stable = (0.95 <= median <= 1.05 and
                  sum(0.90 <= ratio <= 1.10 for ratio in ratios) >= required)
        attempts.append({'pairs': pairs, 'samples': samples, 'median_ratio': median,
                         'within_tolerance': sum(0.90 <= ratio <= 1.10 for ratio in ratios),
                         'required_within_tolerance': required, 'stable': stable,
                         'retried': retry})
        if stable:
            break
        pairs *= 2
    return {'status': 'stable' if attempts[-1]['stable'] else 'inconclusive',
            'pairs_used': attempts[-1]['pairs'], 'attempts': attempts}


def _picop_version() -> str | None:
    try:
        import picop
        return getattr(picop, '__version__', 'installed; version unavailable')
    except ImportError:
        return None


if __name__ == '__main__':
    raise SystemExit(main())
