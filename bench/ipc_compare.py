"""Compare importable SharedByteRing implementations with paired SPSC traffic.

Use the same module for both options for an A/A control; candidate modules
must implement the same public API and shared-memory semantics.
"""
import argparse
import importlib
import json
import multiprocessing as mp
import platform
import statistics
import sys
import time
import os


def cpu_topology(cpu):
    root = f'/sys/devices/system/cpu/cpu{cpu}/topology'
    try:
        with open(f'{root}/physical_package_id') as f:
            package = f.read().strip()
        with open(f'{root}/core_id') as f:
            core = f.read().strip()
        return package, core
    except OSError:
        return None


def choose_cpus(mode, producer_cpu=None, consumer_cpu=None):
    if mode == 'off' or not hasattr(os, 'sched_getaffinity'):
        if producer_cpu is not None or consumer_cpu is not None:
            raise ValueError('explicit CPUs require affinity')
        return None
    available = sorted(os.sched_getaffinity(0))
    if producer_cpu is not None or consumer_cpu is not None:
        if producer_cpu is None or consumer_cpu is None:
            raise ValueError('specify both --producer-cpu and --consumer-cpu')
        if producer_cpu == consumer_cpu or producer_cpu not in available or consumer_cpu not in available:
            raise ValueError('CPUs must be distinct and in the process affinity mask')
        return {'producer': producer_cpu, 'consumer': consumer_cpu}
    if not available:
        return None
    first = available[0]
    topology = cpu_topology(first)
    second = next((cpu for cpu in available[1:]
                   if topology is None or cpu_topology(cpu) != topology), first)
    return {'producer': second, 'consumer': first}


def worker_affinity(enabled, cpus, role):
    if not enabled or cpus is None or not hasattr(os, 'sched_setaffinity'):
        return None
    os.sched_setaffinity(0, {cpus[role]})
    return sorted(os.sched_getaffinity(0))


def control_summary(samples):
    ratios = [sample['ratio'] for sample in samples]
    median = statistics.median(ratios)
    inside = sum(0.90 <= ratio <= 1.10 for ratio in ratios)
    stable = 0.95 <= median <= 1.05 and inside >= 7
    return {'median_ratio': median, 'within_0_90_1_10': inside,
            'stable': stable, 'criterion': 'median 0.95-1.05 and >=7/9 samples 0.90-1.10'}



def timed(call):
    started = time.perf_counter_ns()
    result = call()
    return result, time.perf_counter_ns() - started


def setup_record(role):
    return {'role': role, 'attach_ns': [], 'claim_ns': [], 'release_ns': [], 'close_ns': []}

def ring_class(module, class_name):
    return getattr(importlib.import_module(module), class_name)


def claim_role(ring, role):
    claim = getattr(ring, 'claim', None)
    if claim is not None:
        claim(role)


def release_role(ring, role):
    release = getattr(ring, 'release', None)
    if release is not None:
        release(role)



def consumer(modules, classes, names, pairs, blocks, warmup_blocks, count, size, gate, ack, setup_output, affinity, cpus):
    affinity_result = worker_affinity(affinity, cpus, 'consumer')
    setup = setup_record('consumer')
    setup['cpu_affinity'] = affinity_result
    rings = []
    for module, cls, name in zip(modules, classes, names):
        ring, elapsed = timed(lambda: ring_class(module, cls).attach(name))
        setup['attach_ns'].append(elapsed)
        rings.append(ring)
    for ring in rings:
        _, elapsed = timed(lambda ring=ring: claim_role(ring, 'consumer'))
        setup['claim_ns'].append(elapsed)
    expected = b'x' * size
    try:
        gate.wait()
        for block in range(warmup_blocks):
            for idx in ((0, 1) if block % 2 == 0 else (1, 0)):
                for seq in range(count):
                    item = rings[idx].try_pop()
                    while item is None:
                        item = rings[idx].try_pop()
                    if item[:8] != seq.to_bytes(8, 'little') or item[8:] != expected:
                        raise AssertionError(('warmup', block, idx, seq))
                ack.send(idx)
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
        for ring in rings:
            _, elapsed = timed(lambda ring=ring: release_role(ring, 'consumer'))
            setup['release_ns'].append(elapsed)
            _, elapsed = timed(ring.close)
            setup['close_ns'].append(elapsed)
        setup_output.put(setup)


def producer(modules, classes, names, pairs, blocks, warmup_blocks, count, size, gate, ack, output, setup_output, affinity, cpus):
    affinity_result = worker_affinity(affinity, cpus, 'producer')
    setup = setup_record('producer')
    setup['cpu_affinity'] = affinity_result
    rings = []
    for module, cls, name in zip(modules, classes, names):
        ring, elapsed = timed(lambda: ring_class(module, cls).attach(name))
        setup['attach_ns'].append(elapsed)
        rings.append(ring)
    for ring in rings:
        _, elapsed = timed(lambda ring=ring: claim_role(ring, 'producer'))
        setup['claim_ns'].append(elapsed)
    payload = b'x' * size
    try:
        gate.wait()
        samples = []
        for block in range(warmup_blocks):
            for idx in ((0, 1) if block % 2 == 0 else (1, 0)):
                for seq in range(count):
                    item = seq.to_bytes(8, 'little') + payload
                    while not rings[idx].try_push(item):
                        pass
                if ack.recv() != idx:
                    raise RuntimeError('warmup consumer order mismatch')
        for pair in range(pairs):
            sample_started = time.perf_counter_ns()
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
            samples.append({'baseline_ns': totals[0], 'candidate_ns': totals[1],
                            'sample_elapsed_ns': time.perf_counter_ns() - sample_started,
                            'ratio': totals[1] / totals[0]})
        output.put(samples)
    finally:
        for ring in rings:
            _, elapsed = timed(lambda ring=ring: release_role(ring, 'producer'))
            setup['release_ns'].append(elapsed)
            _, elapsed = timed(ring.close)
            setup['close_ns'].append(elapsed)
        setup_output.put(setup)

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--baseline', required=True); p.add_argument('--candidate', required=True)
    p.add_argument('--baseline-class', default='SharedByteRing')
    p.add_argument('--candidate-class', default='SharedByteRing')
    p.add_argument('--size', type=int, default=128); p.add_argument('--capacity', type=int, default=64)
    p.add_argument('--count', type=int, default=1000, help='messages per ring per block')
    p.add_argument('--blocks', type=int, default=100, help='measured blocks per ring per sample')
    p.add_argument('--warmup-blocks', type=int, default=5,
                   help='unmeasured paired blocks per ring before samples')
    p.add_argument('--pairs', type=int, default=9)
    p.add_argument('--producer-cpu', type=int)
    p.add_argument('--consumer-cpu', type=int)
    p.add_argument('--cpu-affinity', choices=('auto', 'off'), default='auto',
                   help='pin producer and consumer; auto selects different physical cores')
    a = p.parse_args(); modules = (a.baseline, a.candidate)
    classes = (a.baseline_class, a.candidate_class)
    cpus = choose_cpus(a.cpu_affinity, a.producer_cpu, a.consumer_cpu)
    ctx = mp.get_context('spawn')
    parent_setup = {'role': 'parent', 'create_ns': [], 'close_ns': []}
    rings = []
    for module, cls in zip(modules, classes):
        ring, elapsed = timed(lambda module=module, cls=cls:
                              ring_class(module, cls).create(a.capacity, a.size + 8))
        parent_setup['create_ns'].append(elapsed)
        rings.append(ring)
    gate = ctx.Event(); recv, send = ctx.Pipe(False); out = ctx.Queue(); setup_out = ctx.Queue()
    args = (modules, classes, [r.name for r in rings], a.pairs, a.blocks,
            a.warmup_blocks, a.count, a.size, gate)
    pinned = a.cpu_affinity == 'auto'
    ps = [ctx.Process(target=consumer, args=(*args, send, setup_out, pinned, cpus)),
          ctx.Process(target=producer, args=(*args, recv, out, setup_out, pinned, cpus))]
    parent_closed = False
    try:
        for proc in ps: proc.start()
        gate.set()
        samples = out.get(timeout=120)
        for proc in ps: proc.join(10)
        if any(proc.exitcode != 0 for proc in ps): raise RuntimeError('worker failure')
        worker_setup = [setup_out.get(timeout=10), setup_out.get(timeout=10)]
        for ring in rings:
            _, elapsed = timed(ring.close)
            parent_setup['close_ns'].append(elapsed)
        parent_closed = True
        report = {'python':sys.version,'platform':platform.platform(),
                  'machine': platform.machine(), 'processor': platform.processor(),
                  'clock': {'implementation': time.get_clock_info('perf_counter').implementation,
                            'resolution_seconds': time.get_clock_info('perf_counter').resolution},
                  'args':vars(a),
                  'cpu_affinity': {'requested': a.cpu_affinity, 'assigned_cpus': cpus,
                                   'allowed_cpus': sorted(os.sched_getaffinity(0))
                                   if hasattr(os, 'sched_getaffinity') else None},
                  'samples':samples,'median_ratio':statistics.median(x['ratio'] for x in samples),
                  'same_class_control': (control_summary(samples) if classes[0] == classes[1] else None),
                  'setup_timings': {'classes': classes, 'parent': parent_setup,
                                    'workers': worker_setup}}
        print(json.dumps(report, indent=2))
    finally:
        for proc in ps:
            if proc.is_alive(): proc.terminate(); proc.join(5)
        recv.close(); send.close(); out.close(); out.join_thread()
        setup_out.close(); setup_out.join_thread()
        if not parent_closed:
            for ring in rings:
                _, elapsed = timed(ring.close)
                parent_setup['close_ns'].append(elapsed)
        for ring in rings:
            ring.unlink()

if __name__ == '__main__': main()




