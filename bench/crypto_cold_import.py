"""Compare cold imports and first Agent calls using two installed wheel interpreters."""

import json
import os
import statistics
import subprocess
import sys

child = '''
import json, sys, time
start = time.perf_counter_ns()
from picore import crypto
loaded = 'eth_account.messages' in sys.modules
imported = time.perf_counter_ns()
domain = {'name': 'Agent', 'version': '1', 'chainId': 42161,
          'verifyingContract': '0xCcCCccccCCCCcCCCCCCcCcCccCcCCCcCcccccccC'}
expected = 'b4213747a19486f4f2ceb0f7222b9733f612682fd3649afd6a605a21ac36f2da'
def invoke():
    return crypto.eip712_hash_agent_message(domain, 'wss://example.test', bytes(range(32))).hex()
start_fixed = time.perf_counter_ns()
assert invoke() == expected
fixed = time.perf_counter_ns()
fixed_loaded = 'eth_account.messages' in sys.modules
domain['chainId'] = '42161'
start_generic = time.perf_counter_ns()
assert invoke() == expected
generic = time.perf_counter_ns()
assert 'eth_account.messages' in sys.modules
print(json.dumps({'import_ns': imported - start, 'first_fixed_ns': fixed - start_fixed,
                  'first_fallback_ns': generic - start_generic,
                  'loaded_after_import': loaded, 'loaded_after_fixed': fixed_loaded}))
'''

env = {k: v for k, v in os.environ.items() if k != 'PYTHONPATH'}
arms = list(zip(('baseline', 'lazy'), sys.argv[1:], strict=True))
samples = {name: [] for name, _ in arms}
for sample in range(9):
    for name, python in arms if sample % 2 == 0 else reversed(arms):
        samples[name].append(json.loads(subprocess.check_output(
            [python, '-I', '-c', child], cwd='/tmp', env=env, text=True)))
assert all(x['loaded_after_import'] and x['loaded_after_fixed'] for x in samples['baseline'])
assert not any(x['loaded_after_import'] or x['loaded_after_fixed'] for x in samples['lazy'])
print(json.dumps({'raw': samples, 'median_ms': {
    name: {key: statistics.median(x[key] for x in rows) / 1e6
           for key in ('import_ns', 'first_fixed_ns', 'first_fallback_ns')}
    for name, rows in samples.items()}}, indent=2))
