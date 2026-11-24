"""Compare cross-process bounded byte delivery; results are measurements, not speed claims.

Run from the repository root after installing the package, for example:
python bench/ipc.py --count 100000 --payload-size 128
"""

import argparse
import json
import multiprocessing as mp
import os
import platform
import statistics
import sys
import time
from importlib.metadata import PackageNotFoundError, version


def _producer(kind, endpoint, count, payload_size, ready, start, result):
    from picoke.ipc import SharedByteRing

    ring = SharedByteRing.attach(endpoint) if kind == 'shared-byte-ring' else None
    payload = bytes((i % 251 for i in range(payload_size)))
    spins = rejected = 0
    ready.set()
    start.wait()
    began = time.perf_counter_ns()
    for sequence in range(count):
        stamp = time.perf_counter_ns()
        item = sequence.to_bytes(8, 'little') + stamp.to_bytes(8, 'little') + payload
        if kind == 'shared-byte-ring':
            while not ring.try_push(item):
                rejected += 1
                spins += 1
        elif kind == 'queue':
            endpoint.put(item)
        else:
            endpoint.send_bytes(item)
    elapsed = time.perf_counter_ns() - began
    if ring is not None:
        ring.close()
    result.put(('producer', elapsed, spins, rejected))


def _consumer(kind, endpoint, count, payload_size, ready, start, result):
    from picoke.ipc import SharedByteRing

    ring = SharedByteRing.attach(endpoint) if kind == 'shared-byte-ring' else None
    expected_payload = bytes((i % 251 for i in range(payload_size)))
    latencies = []
    spins = 0
    ready.set()
    start.wait()
    for sequence in range(count):
        if kind == 'shared-byte-ring':
            item = ring.try_pop()
            while item is None:
                spins += 1
                item = ring.try_pop()
        elif kind == 'queue':
            item = endpoint.get()
        else:
            item = endpoint.recv_bytes()
        received = time.perf_counter_ns()
        if len(item) != payload_size + 16 or int.from_bytes(item[:8], 'little') != sequence:
            raise AssertionError(f'incorrect item at sequence {sequence}')
        sent = int.from_bytes(item[8:16], 'little')
        if item[16:] != expected_payload:
            raise AssertionError(f'incorrect payload at sequence {sequence}')
        latencies.append(received - sent)
    if ring is not None:
        ring.close()
    result.put(('consumer', spins, latencies))


def _run(kind, count, size, capacity):
    from picoke.ipc import SharedByteRing

    ctx = mp.get_context('spawn')
    ring = SharedByteRing.create(capacity, size + 16) if kind == 'shared-byte-ring' else None
    endpoint = ring.name if ring is not None else (ctx.Queue(maxsize=capacity) if kind == 'queue' else ctx.Pipe(duplex=False))
    if kind == 'pipe':
        receive, send = endpoint
        producer_endpoint, consumer_endpoint = send, receive
    else:
        producer_endpoint = consumer_endpoint = endpoint
    start = ctx.Event()
    producer_ready, consumer_ready = ctx.Event(), ctx.Event()
    results = ctx.Queue()
    consumer = ctx.Process(target=_consumer, args=(kind, consumer_endpoint if ring is None else ring.name, count, size, consumer_ready, start, results))
    producer = ctx.Process(target=_producer, args=(kind, producer_endpoint if ring is None else ring.name, count, size, producer_ready, start, results))
    try:
        consumer.start()
        producer.start()
        if not producer_ready.wait(30) or not consumer_ready.wait(30):
            raise RuntimeError(f'{kind} workers did not become ready')
        began = time.perf_counter_ns()
        start.set()
        messages = {}
        deadline = time.monotonic() + max(30, count / 1000)
        while len(messages) < 2 and time.monotonic() < deadline:
            try:
                message = results.get(timeout=min(0.5, max(0.01, deadline - time.monotonic())))
            except Exception:
                if (producer.exitcode is not None and producer.exitcode != 0) or (consumer.exitcode is not None and consumer.exitcode != 0):
                    break
                continue
            messages[message[0]] = message
        if len(messages) != 2:
            raise RuntimeError(f'{kind} workers failed or timed out (producer={producer.exitcode}, consumer={consumer.exitcode})')
        producer.join(30)
        consumer.join(30)
        elapsed = time.perf_counter_ns() - began
        if producer.is_alive() or consumer.is_alive() or producer.exitcode or consumer.exitcode:
            raise RuntimeError(f'{kind} child failed (producer={producer.exitcode}, consumer={consumer.exitcode})')
        _, producer_ns, producer_spins, rejected = messages['producer']
        _, consumer_spins, latencies = messages['consumer']
        return {
            'elapsed_ns': elapsed, 'producer_ns': producer_ns,
            'items_per_second': count * 1e9 / elapsed,
            'latency_ns': {'median': statistics.median(latencies), 'p95': sorted(latencies)[int(.95 * (count - 1))], 'max': max(latencies)},
            'producer_full_retries': rejected, 'producer_retry_spins': producer_spins,
            'consumer_empty_spins': consumer_spins,
            'verified_items': count, 'verified_payload_bytes': count * size,
        }
    finally:
        start.set()
        for process in (producer, consumer):
            if process.pid is not None and process.is_alive():
                process.terminate()
            if process.pid is not None:
                process.join(5)
        results.close()
        results.join_thread()
        if kind == 'queue':
            endpoint.close()
            endpoint.join_thread()
        elif kind == 'pipe':
            receive.close()
            send.close()
        if ring is not None:
            ring.close()
            ring.unlink()




def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--count', type=int, default=10000)
    parser.add_argument('--payload-size', type=int, default=128)
    parser.add_argument('--capacity', type=int, default=256)
    parser.add_argument('--samples', type=int, default=3)
    parser.add_argument('--backends', nargs='+', choices=('shared-byte-ring', 'queue', 'pipe'), default=('shared-byte-ring', 'queue', 'pipe'))
    args = parser.parse_args()
    if args.count < 1 or args.payload_size < 0 or args.capacity < 1 or args.samples < 1:
        parser.error('count, capacity, and samples must be positive; payload-size must be nonnegative')
    try:
        package_version = version('picoke')
    except PackageNotFoundError:
        package_version = None
    report = {
        'python': sys.version, 'implementation': platform.python_implementation(),
        'platform': platform.platform(), 'cpu_count': os.cpu_count(),
        'gil_enabled': sys._is_gil_enabled() if hasattr(sys, '_is_gil_enabled') else None,
        'picoke': package_version, 'arguments': vars(args),
        'method': 'one spawned producer and one consumer; child imports/attachment excluded by readiness events; elapsed includes delivery, validation, and result collection; busy-spin on ring full/empty; baselines block in stdlib; latency uses shared monotonic clock; each received sequence and payload is byte-checked',
        'samples': {},
    }
    for kind in args.backends:
        rows = [_run(kind, args.count, args.payload_size, args.capacity) for _ in range(args.samples)]
        report['samples'][kind] = rows
        report.setdefault('medians', {})[kind] = {
            key: statistics.median(row[key] for row in rows)
            for key in ('elapsed_ns', 'producer_ns', 'items_per_second', 'producer_full_retries', 'producer_retry_spins', 'consumer_empty_spins')
        }
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
