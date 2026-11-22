# Crypto performance comparisons

These measurements used a focused historical Cython baseline with minimal package initializers and build configuration, not a full distribution. Reproducing that build requires a compatible source archive and license, a C compiler, OpenSSL headers/libcrypto, Cython, and picop. The build tool requires a fresh output directory and preserves the source license.

From the repository root, install `picoke`, Cython and setuptools separately
under each interpreter, then run the checked-in benchmark scripts directly:

```bash
.venv315/bin/python scripts/prepare_legacy_crypto.py \
  --archive "$LEGACY_ARCHIVE" --source-root "$LEGACY_SOURCE_ROOT" --output /tmp/legacy-crypto-315
.venv315/bin/python bench/crypto.py --legacy-root /tmp/legacy-crypto-315 --samples 7 --iterations 20 --warmups 1
.venv314/bin/python scripts/prepare_legacy_crypto.py \
  --archive "$LEGACY_ARCHIVE" --source-root "$LEGACY_SOURCE_ROOT" --output /tmp/legacy-crypto-314
.venv314/bin/python bench/crypto.py --legacy-root /tmp/legacy-crypto-314 --samples 7 --iterations 20 --warmups 1
.venv315/bin/python bench/crypto_protocols.py --legacy-root /tmp/legacy-crypto-315
.venv314/bin/python bench/crypto_protocols.py --legacy-root /tmp/legacy-crypto-314
```

These current runners import `picoke`; the `picore` labels and paths below
describe archived measurements and are not current install instructions.
Historical reports under `results/` are immutable. To reproduce a measurement
explicitly tied to an old picore snapshot, check out that snapshot (or extract
its source archive) and run its pre-rename runner from that tree, using the
original commands and `/tmp/picore-*` paths recorded below. Do not run the
current `picoke` runners against an old snapshot or overwrite archived result
files; preserve historical labels and report schemas when comparing results.

Run measurements sequentially to avoid mutual interference. The harness checks matching outputs before timing; legacy decoding of a trailing NUL is checked separately and excluded from timing. Ed25519 verification compares valid signatures, not acceptance policies for adversarial inputs; the direct provider has a weaker acceptance policy. Signing includes a direct provider arm that imports the same raw seed for each call and a separately labeled pre-imported-key arm. Legacy behavior also differs for Ethereum address derivation (legacy hashes the SEC `0x04` prefix; Ethereum/picore hash coordinates only) and Bitcoin message hashing (legacy hashes raw input once; picore uses framed double SHA-256). These are intentional contract differences, not equivalent timing comparisons. The Bitcoin framed-hash benchmark instead uses an independent stdlib reference. Signature timing is excluded if the legacy and picore `(r, s, v)` outputs differ, even when both cross-recover correctly. Individual paired sample times and ratios are reported alongside the median and spread.

## Non-Base64 crypto continuation

The historical Cython build and installed picore wheel ran on
CPython 3.14.7 and 3.15.0rc2 on Linux x86-64 (Intel i7-6700).
Both wheels passed 372 tests outside the checkout. Raw reports list
loaded extension origins, provider versions, and seven alternating
samples of 20 calls each:

- 3.14.7: [hashes/Ed25519](results/crypto-next-hashes-3.14.txt),
  [protocols](results/crypto-next-protocols-3.14.txt).
- 3.15.0rc2: [hashes/Ed25519](results/crypto-next-hashes-3.15.txt),
  [protocols](results/crypto-next-protocols-3.15.txt).

The legacy Keccak digest differed at 135 and 136 bytes on both interpreters.
The legacy padding code appends two bytes when only one remains and omits
the padding block for nonempty inputs aligned to the 136-byte rate. Those
legacy timing arms were excluded. Picore's rate-boundary vectors match
independently generated noble digests. Legacy secp256k1 signing was also
excluded: the signature tuples differ even though both cross-recover.

The measured legacy comparison favors picore for secp256k1 recovery and
Ed25519 signing; the Agent EIP-712 path is slower on both interpreters and
is the next performance target. The comparison covers one Agent input and
does not establish validation parity with legacy. No provider switches or
picop substitutions were made. No final CPython 3.15 or free-threaded
measurement, and no independent security audit.

## EIP-712 Agent wheel comparison

Build and install the two revisions in separate virtual environments with the
same CPython version and pinned dependencies. Run each interpreter pair
separately from the checkout:

```bash
python3.15 bench/eip712_agent_compare.py \
  --baseline-python "$BASELINE_PYTHON" --candidate-python "$CANDIDATE_PYTHON" \
  --samples 9 --iterations 1000 --warmups 2 > agent-comparison.json
```

The script preflights matching digests and native-wheel origins before timing.
Samples run serially with alternating wheel order. The report includes raw
samples, paired candidate/baseline ratios, and historical Agent attribution
against the public eth-account encoder. Supply the checkpoint interpreter to
both arms for attribution alone. Add `--profile` for a separate 1,000-call
profile of each attribution arm. Direct provider timing omits picore
validation and is not a replacement benchmark.

Historical baseline build, eth-account 0.14.0, Intel i7-6700, Linux x86-64.
Each report uses the same installed wheel in both arms; its ratios between
arms show measurement variation, not a candidate speedup.

| CPython | Historical Agent median | Direct provider median | Direct / picore | Raw samples and profiles |
|---|---:|---:|---:|---|
| 3.14.7 | 349.5 µs | 311.5 µs | 89.1% | [JSON](results/eip712-agent-attribution-3.14.json) |
| 3.15.0rc2 | 354.9 µs | 355.0 µs | 100.0% | [JSON](results/eip712-agent-attribution-3.15.json) |

The 3.15 result missed the eligibility gate (direct ≤90% of picore), so no
Cython shortcut was prototyped. On 3.15 `cProfile` attributes 1.308 of 1.429
profiled seconds to `encode_typed_data` in the picore arm; profiled durations
do not enter the performance gate. The unchanged installed wheels passed 383
tests per interpreter.

### Fixed-schema Agent encoder

The retained encoder prehashes the two Agent type signatures and packs the
fixed EIP-712 fields in Cython. It reuses picore's Keccak-256 implementation and
keeps the eth-account path for inputs outside the canonical fast path. The
generic full-message API is unchanged.

The initial separate-process comparison showed Agent wins on both versions
but failed generic-control gates. The same-wheel attribution runs above also
failed those controls, so the separate-process samples could not distinguish
regressions from timing variation. A second comparison pinned both workers
to CPU 1 and added `--paired-block-size 25`: each measured sample still has
1,000 calls per arm, but alternates wheel order between 25-call blocks.
The two workers warm up separately before timing; IPC is outside the measured
blocks. The acceptance thresholds are unchanged.

Initial samples: [3.14](results/eip712-agent-native-separate-3.14.json),
[3.15](results/eip712-agent-native-separate-3.15.json).

| CPython | Agent workload | Checkpoint median | Native median | Paired speedup |
|---|---|---:|---:|---:|
| 3.14.7 | Historical | 346.1 µs | 78.7 µs | 4.52× |
| 3.14.7 | ethers vector | 332.3 µs | 71.5 µs | 4.51× |
| 3.14.7 | Unicode | 311.4 µs | 68.1 µs | 4.59× |
| 3.15.0rc2 | Historical | 332.6 µs | 74.1 µs | 4.67× |
| 3.15.0rc2 | ethers vector | 340.8 µs | 72.7 µs | 4.54× |
| 3.15.0rc2 | Unicode | 331.2 µs | 65.7 µs | 4.63× |

Paired speedup is the reciprocal of the median candidate/baseline ratio, not
the quotient of the two separate medians. All nine pairs per Agent workload
beat the 5% threshold on both interpreters. Mail and both array controls had
median paired ratios from 0.981 to 1.011 and no more than one pair per control
above 1.10. The retained wheels passed 398 tests each outside the checkout.

Interleaved samples: [3.14](results/eip712-agent-native-interleaved-3.14.json),
[3.15](results/eip712-agent-native-interleaved-3.15.json).

## Python 3.15 Base64 decode dispatch

For exact `str` inputs, CPython 3.14 and 3.15 now use pybase64 from **2048
encoded characters**. Smaller inputs retain their earlier EVP/stdlib paths;
CPython 3.16+ conservatively uses stdlib. Provider rejection falls back to
strict `binascii`, and subclasses retain stdlib behavior. The cutoff is a
host-specific performance decision, not a public API guarantee.

To reduce the drift in earlier separate-process comparisons, the following
measurements load the previous and updated compiled wheels as distinct
extension modules in each fresh process. Each of nine processes alternates
and rotates seven paired timing blocks after warmup. Mean ± sample standard
deviation across processes, `b64decode`, CPython 3.15.0rc2:

| Decoded bytes | Padding | Previous wheel | Updated wheel |
|---:|---:|---:|---:|
| 1534 | 2 | 2165 ± 416 ns | 1352 ± 318 ns |
| 1535 | 1 | 2149 ± 648 ns | 1326 ± 399 ns |
| 1536 | 0 | 2153 ± 486 ns | 1286 ± 315 ns |
| 2047 | 2 | 2752 ± 591 ns | 1395 ± 479 ns |
| 2048 | 1 | 2909 ± 487 ns | 1452 ± 346 ns |
| 2049 | 0 | 2554 ± 534 ns | 1317 ± 320 ns |

All nine process-level pairs favored the updated wheel by at least 5% for
both decode spellings at these six sizes. At 1537 bytes, one of nine pairs
for `b64decode` slightly favored the previous wheel despite a lower updated
mean; the ASCII spelling favored the update in all nine. Controls below and
above the changed range were inconclusive, not proof of a performance win.
On 3.14, the same paired wheel comparison found no consistent regression in
the sampled controls, though some means varied substantially.

The host was Linux x86-64 on an Intel i7-6700, CPU 4 affinity, pybase64
1.5.2, and CPython 3.15.0rc2 / 3.14.7. The frequency governor was
`powersave`, so affinity did not fix frequency. These are Python-call
microbenchmarks, not statistical significance or cross-platform guarantees.
Both installed wheels passed the full 338-test crypto suite. The local `uv`
installer had no download for CPython 3.15.2; no released 3.15 build was
tested here.

Full installed-wheel reports:
[3.15 decode JSON](results/decode315-installed-b64decode-cpython315rc2.json) /
[text](results/decode315-installed-b64decode-cpython315rc2.txt),
[3.15 ASCII JSON](results/decode315-installed-b64decode_ascii-cpython315rc2.json) /
[text](results/decode315-installed-b64decode_ascii-cpython315rc2.txt);
[3.14 decode control](results/decode315-control-b64decode-cpython314.json) and
[ASCII control](results/decode315-control-b64decode_ascii-cpython314.json).
The earlier [interleaved prototype study](results/decode315-interleaved-cpython315rc2.json)
contains the other cutoff candidates and strict preflight results.

The recorded 3.15 command, from `picore/`, was run once for each API:

```bash
P=/path/to/venv315/bin/python
D=/tmp/decode315-experiment
mkdir -p "$D"
cp bench/_decode315_candidate.pyx "$D/"
(cd "$D" && "$P" -m Cython.Build.Cythonize -i _decode315_candidate.pyx)
for API in b64decode b64decode_ascii; do
  taskset -c 4 "$P" bench/decode315_compare.py --python "$P" \
    --candidate-root /tmp/picore-decode315-experiment/rc2 \
    --baseline-root /tmp/picore-threshold-retained/root315 \
    --comparison-root /tmp/picore-decode315-final/root315 --api "$API" \
    --samples 9 --blocks 7 --target-ms 3 \
    --sizes 1024 1533 1534 1535 1536 1537 1538 2046 2047 2048 2049 2303 2304 2305 2688 3071 3072 65536 \
    --json-output bench/results/decode315-installed-$API-cpython315rc2.json
done
```

The temporary candidate extension and prior/updated wheels must be rebuilt to
reproduce the run. The JSON includes paths and SHA-256 hashes for both wheels'
loaded extensions, configuration, preflight probes and paired samples.

## Prior Base64 decode dispatch decision

At the time of the following measurements, CPython 3.14 used pybase64 at
2048 encoded characters and CPython 3.15 retained the 4096-character
threshold. That 3.15 decision was subsequently revisited above.

The retained 3.14 wheel, measured against the prior wheel, gave the following
mean ± sample standard deviation for `b64decode`:

| Decoded bytes | Padding | Before | After |
|---:|---:|---:|---:|
| 1534 | 2 | 3335 ± 800 ns | 1110 ± 322 ns |
| 1535 | 1 | 3831 ± 587 ns | 955 ± 365 ns |
| 1536 | 0 | 3769 ± 708 ns | 1215 ± 377 ns |
| 1537 | 2 | 3562 ± 838 ns | 1161 ± 412 ns |
| 1538 | 1 | 3572 ± 624 ns | 1340 ± 373 ns |
| 2047 | 2 | 4644 ± 956 ns | 1331 ± 462 ns |

All seven paired rounds for both decode spellings at these six sizes were
at least 5% faster than the previous wheel. The 1533-byte payload, below the
new encoded-length cutoff, remained on the old path and was inconclusive.
The provisional lower cutoff on 3.15 improved means in several cases but did
not consistently pass the paired gate, so it was not retained. A subsequent
experimental eager length-read also showed an apparent repeatable 3.15
regression in one workload; that incidental change was removed, and the
retained wheel's paired results for that workload were inconclusive.

The retained comparison used isolated installed wheels on CPython 3.14.7 and
3.15.0rc2, pybase64 1.5.2, Linux x86-64 on an Intel i7-6700 pinned to CPU 4,
seven alternating paired rounds and 10 ms target batches. Both retained
wheels passed the 255-case Base64 suite; the immediately preceding wheel
variant passed the 338-case full crypto suite on each interpreter. Results
remain noisy and do not generalize to other machines or runtimes.

Complete retained reports: [3.14 JSON](results/base64-threshold-retained-cpython314.json) /
[text](results/base64-threshold-retained-cpython314.txt),
[3.15 JSON](results/base64-threshold-retained-cpython315.json) /
[text](results/base64-threshold-retained-cpython315.txt).
The provisional lower-cutoff-on-both-interpreters run is preserved for
[3.14](results/base64-threshold-cpython314.json) and
[3.15](results/base64-threshold-cpython315.json); the intermediate
eager-length-read run is preserved for
[3.14](results/base64-threshold-final-cpython314.json) and
[3.15](results/base64-threshold-final-cpython315.json).

The retained reports were produced from `picore/` using the temporary wheel
and baseline roots recorded in their JSON:

```bash
for V in 314 315; do
  P=/path/to/venv$V/bin/python
  if [ "$V" = 314 ]; then L=/path/to/legacy314/crypto; else L=/path/to/legacy315/crypto; fi
  taskset -c 4 "$P" bench/base64_compare.py --python "$P" \
    --candidate-root /tmp/picore-threshold-retained/root$V \
    --baseline-root /tmp/picore-threshold-prior/$V --legacy-root "$L" \
    --samples 7 --sizes 1533 1534 1535 1536 1537 1538 2047 --target-ms 10 \
    --json-output bench/results/base64-threshold-retained-cpython$V.json
done
```

Reproduction requires rebuilding the temporary prior and retained wheels and
legacy trees; their exact extension hashes and mapped libraries are in the
reports.

## Padding crossover and pybase64 call overhead

Additional experiments compare the OpenSSL 4.2.0-dev `memchr` candidate
described below with a standalone Cython wrapper that calls the public
pybase64 API even below picore's previous 4096-character dispatch threshold.
For each decoded size, the harness validates outputs and strict error
behavior before timing; adjacent sizes exercise zero, one and two padding
characters. On CPython 3.15.0rc2, means for the three 1536–1538-byte
payloads were:

| Decoded bytes | Padding characters | OpenSSL AVX2 + memchr | pybase64 wrapper |
|---:|---:|---:|---:|
| 1536 | 0 | 443 ns | 1415 ns |
| 1537 | 2 | 551 ns | 1742 ns |
| 1538 | 1 | 513 ns | 1363 ns |

On this host the OpenSSL development build won all seven paired rounds for
these 3.15 cases; results varied with padding and size, and the crossover
was not stable at larger sizes. These measurements do not justify requiring
an unreleased OpenSSL: a version or CPU check alone cannot establish that a
distribution's loaded library contains or selects the tested decoder.
OpenSSL 3.x and 4.x are also different ABIs.

Caching the pybase64 Python callable or explicitly using CPython vectorcall
did not produce a consistent improvement across interpreters and workloads,
so neither prototype was adopted. pybase64 documents a Python API but no
public C ABI for its bundled libbase64; the exported symbols in a particular
wheel are not a supported interface.

Both comparisons used CPython 3.14.7 and 3.15.0rc2, pybase64 1.5.2 and the
same AVX2-capable i7-6700 host, CPU 4 affinity, seven alternating paired
rounds, fresh processes and 10 ms target batches. The reports retain all
samples and correctness probes:
[direct-provider 3.14 JSON](results/openssl-provider-crossover-cpython314.json) /
[text](results/openssl-provider-crossover-cpython314.txt),
[3.15 JSON](results/openssl-provider-crossover-cpython315.json) /
[text](results/openssl-provider-crossover-cpython315.txt);
[wrapper 3.14 JSON](results/pybase64-wrapper-cpython314.json) /
[text](results/pybase64-wrapper-cpython314.txt),
[3.15 JSON](results/pybase64-wrapper-cpython315.json) /
[text](results/pybase64-wrapper-cpython315.txt).
An earlier wider comparison against the then-current picore dispatch is
available for [3.14](results/openssl-crossover-cpython314.json) and
[3.15](results/openssl-crossover-cpython315.json); below 4096 encoded
characters that picore baseline did not call pybase64.

To recreate these runs, first install the pre-change picore wheel and build
the experimental OpenSSL and candidate as described in the next section, then
from `picore/` run:

```bash
for V in 314 315; do
  P=/tmp/picore-cython-artifacts.ivNMGH6a/py$V/check/bin/python
  D=/tmp/picore-wrapper-candidate/$V
  mkdir -p "$D"
  cp bench/_pybase64_wrapper_candidate.pyx "$D/"
  (cd "$D" && "$P" -m Cython.Build.Cythonize -i _pybase64_wrapper_candidate.pyx)
  taskset -c 4 "$P" bench/pybase64_wrapper_compare.py --python "$P" \
    --candidate-root "$D" --samples 7 --target-ms 10 \
    --sizes 1023 1024 1025 1533 1534 1535 1536 1537 1538 2046 2047 2048 3072 3073 3074 65536 \
    --json-output bench/results/pybase64-wrapper-cpython$V.json
  taskset -c 4 "$P" bench/openssl_simd_compare.py --python "$P" \
    --candidate-root /tmp/picore-openssl-simd/candidate$V \
    --openssl-libdir /tmp/picore-openssl-simd/install/lib --wrapper-root "$D" \
    --backends openssl_memchr picore pybase64_wrapper binascii --samples 7 \
    --sizes 1536 1537 1538 2046 2047 2048 3072 3073 3074 6144 6145 6146 12288 12289 12290 32766 32767 32768 \
    --target-ms 10 --json-output bench/results/openssl-provider-crossover-cpython$V.json
done
```


## Experimental OpenSSL AVX2 versus pybase64

This compares complete strict `b64decode` calls, not raw EVP throughput.
The OpenSSL candidates are benchmark-only; production remains unchanged.
Both candidates use a pinned OpenSSL development snapshot (4.2.0-dev),
with `strspn`; the experimental second uses `memchr` to reject interior
padding, explicitly validates the final quartet, and relies on EVP failure
and the returned output length for the remaining alphabet/whitespace checks.
Both delegate unusual cases to strict `binascii` for exact error behavior and
decode the padded tail separately. This does not establish correctness for
other OpenSSL versions.

Means ± sample standard deviation, microseconds per `b64decode` call:

| CPython | Decoded payload | OpenSSL + strspn | OpenSSL + memchr | Current picore / pybase64 |
|---|---:|---:|---:|---:|
| 3.14.7 | 3 KiB | 2.31 ± 0.38 | 0.75 ± 0.13 | 1.22 ± 0.24 |
| 3.14.7 | 64 KiB | 43.29 ± 7.15 | 11.72 ± 1.03 | 9.33 ± 2.34 |
| 3.14.7 | 1 MiB | 789.23 ± 155.16 | 192.98 ± 24.28 | 140.13 ± 38.95 |
| 3.14.7 | 8 MiB | 7993.01 ± 985.99 | 3111.66 ± 270.91 | 2200.73 ± 282.13 |
| 3.15.0rc2 | 3 KiB | 2.42 ± 0.23 | 0.72 ± 0.15 | 1.67 ± 0.32 |
| 3.15.0rc2 | 64 KiB | 48.80 ± 1.03 | 11.56 ± 0.73 | 10.86 ± 2.98 |
| 3.15.0rc2 | 1 MiB | 711.58 ± 62.66 | 198.38 ± 20.46 | 156.35 ± 47.47 |
| 3.15.0rc2 | 8 MiB | 7977.46 ± 882.87 | 3089.56 ± 244.88 | 2212.48 ± 240.78 |

The memchr candidate beat picore by at least 5% in every paired round at
3 KiB on both interpreters, and was at least 5% slower in every round at
8 MiB. At 64 KiB and 1 MiB the paired results were inconsistent. This uses
one deterministic binary payload per size; the 3 KiB timing has no padding.
The full `strspn` scan was consistently slower than pybase64 in these cases.
These observations do not establish a general winner on other CPUs, OpenSSL
versions, data distributions or workloads.

The Linux x86-64 host was an Intel i7-6700 pinned to CPU 4. Each interpreter
used seven alternating paired rounds, fresh isolated processes, 20 ms target
batches, and two warmups; iteration caps can shorten batches. Both the
experimental OpenSSL path and pybase64 1.5.2 used AVX2. The OpenSSL build
used GCC 15.2.0 without global `-mavx2`; disassembly showed the runtime
capability check and AVX2 decoder call. In fresh processes, reading that
build's capability word confirmed the AVX2 bit was enabled normally and
cleared with `OPENSSL_ia32cap=:~0x20:~0x0:~0x0:~0x0`. The JSON also records
both AVX2-disabled OpenSSL controls, including the `memchr` variant, and
their complete timings. The loaded development `libcrypto.so.4` was checked
in each OpenSSL worker and had SHA-256
`45d1525332bcc26ec1eac12f1cebcc2a253a02259bff2ad0e8d2ddb68dd912bc`.

Before timing, the harness required exact type, result and exception parity
for binary, malformed, padding, non-ASCII and subclass cases. An additional
56,419 differential inputs per interpreter and OpenSSL dispatch mode matched
strict `binascii`, including boundary lengths and terminal zero bytes. These
checks are not an independent security audit.

Raw paired samples, provider paths, hashes, configuration and controls:
[3.14 JSON](results/openssl-vs-pybase64-cpython314.json) /
[text](results/openssl-vs-pybase64-cpython314.txt);
[3.15 JSON](results/openssl-vs-pybase64-cpython315.json) /
[text](results/openssl-vs-pybase64-cpython315.txt).

To reproduce, obtain the same OpenSSL development snapshot in an isolated
source directory, then build OpenSSL and the benchmark extension separately for each Python:

```bash
PICORE="$PWD"  # Start in the picore checkout.
cd /tmp/openssl-simd/src
./config shared --prefix=/tmp/openssl-simd/install --libdir=lib
make -j8 build_libs
make install_dev
cd "$PICORE"

# From picore/, with Cython and setuptools installed in each environment:
for V in 314 315; do
  P=/path/to/venv$V/bin/python
  D=/tmp/openssl-simd/candidate$V
  mkdir -p "$D"
  cp bench/_openssl_simd_candidate.pyx "$D/"
(cd "$D" && "$P" -c 'from setuptools import setup,Extension; from Cython.Build import cythonize; p="/tmp/openssl-simd/install"; setup(name="simd_experiment",ext_modules=cythonize([Extension("_openssl_simd_candidate",["_openssl_simd_candidate.pyx"],include_dirs=[p+"/include"],library_dirs=[p+"/lib"],libraries=["crypto"],runtime_library_dirs=[p+"/lib"])],compiler_directives={"language_level":3,"annotation_typing":False}))' build_ext --inplace)
  taskset -c 4 "$P" bench/openssl_simd_compare.py --python "$P" \
    --candidate-root "$D" --openssl-libdir /tmp/openssl-simd/install/lib \
    --samples 7 --target-ms 20 \
    --json-output bench/results/openssl-vs-pybase64-cpython$V.json
done
```

The build and installed wheels were temporary. Reproduction requires the same
OpenSSL development snapshot, matching Python installations and a rebuild
of the pre-comparison wheel; library paths and ABI are platform-specific.

## Historical SIMD Base64 large-decode results

At the time of these measurements, exact `str` inputs of at least 4096 encoded
characters on CPython 3.14 and 3.15 used `pybase64`'s validating libbase64
decoder. Provider rejections went to strict `binascii` for the running
interpreter's result or error; subclasses retained the stdlib path. Smaller
inputs and CPython 3.16+ used the earlier EVP/stdlib dispatch. The threshold
was a measured host-specific choice, not a portable performance guarantee.
`pybase64>=1.5.2,<1.6` is an additional runtime dependency; source builds may
need a native compiler. The provider selects a supported implementation for
the CPU; these results used AVX2.

The table shows `b64decode` mean ± sample standard deviation in microseconds,
for decoded payload sizes. The previous picore wheel is the pre-SIMD hybrid.
Legacy is timed only on equivalent outputs; it still loses terminal NUL bytes.
The stdlib column includes the equivalent public type guard and conversion.

| CPython | Payload | SIMD picore | Previous picore | Legacy | Stdlib |
|---|---:|---:|---:|---:|---:|
| 3.14.7 | 3 KiB | 1.53 ± 0.41 | 7.16 ± 1.45 | 5.76 ± 0.81 | 16.50 ± 4.56 |
| 3.14.7 | 64 KiB | 9.91 ± 1.31 | 140.45 ± 29.06 | 118.48 ± 12.82 | 417.04 ± 119.22 |
| 3.14.7 | 1 MiB | 135.43 ± 20.20 | 2428.10 ± 290.31 | 2000.58 ± 266.78 | 7363.72 ± 2341.42 |
| 3.14.7 | 8 MiB | 1659.00 ± 41.46 | 17922.36 ± 2400.12 | 15399.65 ± 1280.03 | 62494.14 ± 13591.49 |
| 3.15.0rc2 | 3 KiB | 1.70 ± 0.34 | 4.07 ± 0.92 | 5.67 ± 0.77 | 4.93 ± 0.81 |
| 3.15.0rc2 | 64 KiB | 10.19 ± 1.76 | 68.57 ± 10.77 | 116.12 ± 14.06 | 72.29 ± 17.76 |
| 3.15.0rc2 | 1 MiB | 139.67 ± 10.34 | 1210.37 ± 186.18 | 1864.17 ± 243.80 | 1364.83 ± 266.64 |
| 3.15.0rc2 | 8 MiB | 1901.69 ± 140.22 | 11273.01 ± 1874.31 | 15654.09 ± 2334.22 | 11409.93 ± 1819.98 |

All seven paired rounds were at least 5% faster than previous picore for
both decode spellings at these sizes. Below the SIMD threshold the differences
were inconclusive; some unchanged encoding cases also varied. These are
microbenchmarks, not workload-wide or cross-platform guarantees. Runs used
CPython 3.14.7 and 3.15.0rc2, Cython 3.3.1, pybase64 1.5.2, Linux x86-64 on
an Intel Core i7-6700 pinned to CPU 4, seven alternating fresh-process rounds,
and 20 ms target batches. The 8 MiB iteration cap can shorten a batch.

Full provenance, probes, paired samples and all encoding/decoding cases:
[3.14 JSON](results/base64-simd-cpython314.json) /
[text](results/base64-simd-cpython314.txt);
[3.15 JSON](results/base64-simd-cpython315.json) /
[text](results/base64-simd-cpython315.txt). The pre-SIMD wheels and legacy
builds used for these runs were temporary; reproducing the exact comparison
requires rebuilding those snapshots. The commands, from `picore/`, were:

```bash
P=/path/to/venv314/bin/python
taskset -c 4 "$P" bench/base64_compare.py --python "$P" \
  --baseline-root /tmp/baseline-large/314 \
  --legacy-root /tmp/legacy314/crypto --samples 7 \
  --sizes 12 1024 3072 65536 1048576 8388608 --target-ms 20 \
  --json-output bench/results/base64-simd-cpython314.json
P=/path/to/venv315/bin/python
taskset -c 4 "$P" bench/base64_compare.py --python "$P" \
  --baseline-root /tmp/baseline-large/315 \
  --legacy-root /tmp/legacy315/crypto --samples 7 \
  --sizes 12 1024 3072 65536 1048576 8388608 --target-ms 20 \
  --json-output bench/results/base64-simd-cpython315.json
```

## Historical OpenSSL EVP Base64 baseline (before SIMD decode)

This section records the implementation and measurements before the large-input
SIMD change. Its dispatch descriptions and tables are historical.

The Base64 implementation is Cython and calls OpenSSL `EVP_EncodeBlock` and
`EVP_DecodeBlock` directly for eligible exact built-in `bytes`/`str` inputs.
It allocates the final Python result and fills it in place; decode handles the
final padded quartet separately so EVP's three-byte write cannot overrun the
exact-size output or erase valid trailing zero bytes. Strict decoding delegates
non-ASCII or unusual syntax to the runtime's strict `binascii` implementation,
preserving its error policy. The native extension links to `libcrypto` at build
time; deployment also needs a compatible OpenSSL `libcrypto` available to the
dynamic loader. It does not bundle a private OpenSSL copy.

The public contract is exact: encode accepts `bytes` (including bytes
subclasses), decode accepts `str` (including str subclasses); other types raise
`TypeError`. Encoding uses standard Base64 alphabet/padding with no line breaks;
decoding is strict, rejecting invalid alphabet and whitespace; padding validity
and the exact error follow `binascii.a2b_base64(..., strict_mode=True)`, including
any version-specific acceptance of excess padding, rather than a separate
hand-written rule. `b64encode` returns bytes, `b64encode_ascii` returns str,
and both decode spellings return bytes. ASCII spelling on decode is an alias,
not a bytes-input API. Decode preserves embedded and terminal NUL bytes. The
legacy EVP decoder had a trailing-NUL defect: it returned `b""` for decoded
`b"\0"` and stripped terminal zero bytes from other binary payloads. Those
legacy outputs are correctness differences, excluded from timing parity.

Dispatch is runtime-specific and intentionally conservative:

* CPython 3.14: EVP for eligible exact inputs; stdlib for inputs outside EVP's
  representable size bounds or strict-decoding edge cases.
* CPython 3.15.0rc2: encode uses the prior `binascii` implementation for
  `b64encode` at input length >= 2048 bytes, and for decode at encoded text
  length >= 64 characters. EVP remains selected below those thresholds;
  `b64encode_ascii` does not use the bytes-encode threshold because measurements
  did not show the same crossover for the str result. This rc2 result is not
  evidence that the same thresholds are optimal on final 3.15 or other builds.

The 3.15 thresholds are measured crossover points, not API promises or
universal optima. In controlled paired runs on Linux x86-64, Intel Core
i7-6700, CPU 4 affinity, seven alternating rounds, target 20 ms, candidate
versus the previous CPython `binascii` implementation, decode performance
crossed over between payload sizes 32 and 64 bytes (44 and 88 encoded
characters, respectively); bytes encoding crossed between 1 KiB and 2 KiB.
The final full-workload results and complete raw reports are [`CPython 3.14.7`
JSON](results/base64-evp-final-cpython314.json) / [text](results/base64-evp-final-cpython314.txt)
and [`CPython 3.15.0rc2` JSON](results/base64-evp-final-cpython315.json) /
[text](results/base64-evp-final-cpython315.txt). They record candidate extension
SHA-256, extension origin, mapped `libcrypto` path and environment provenance.

Both runs used Linux x86-64 on an Intel Core i7-6700 pinned to CPU 4;
the reports identify Cython 3.3.1, OpenSSL 3.5.8 and mapped
`/usr/lib/x86_64-linux-gnu/libcrypto.so.3` (the loaded library path, rather
than an inferred provider version).
Earlier final-hybrid runs
([3.14](results/base64-evp-hybrid-cpython314.txt),
[3.15](results/base64-evp-hybrid-cpython315.txt)) and initial all-EVP runs
([3.14](results/base64-evp-initial-cpython314.txt),
[3.15](results/base64-evp-initial-cpython315.txt)) are retained as historical
snapshots, superseded for the final comparison by the reports above.

Mean ± sample standard deviation, ns/call, candidate vs previous CPython
`binascii` (all values below rounded from the final JSON summaries):

| Workload | 3.14.7 candidate vs previous | 3.15.0rc2 candidate vs previous |
|---|---:|---:|
| encode bytes, 12 B | 144 ± 40 vs 356 ± 123 | 118 ± 49 vs 407 ± 119 |
| encode ASCII, 12 B | 175 ± 64 vs 421 ± 138 | 146 ± 43 vs 518 ± 143 |
| decode, 12 B | 279 ± 101 vs 323 ± 94 | 230 ± 67 vs 269 ± 89 |
| decode ASCII, 12 B | 291 ± 55 vs 366 ± 131 | 259 ± 87 vs 314 ± 82 |
| encode bytes, 1 KiB | 1520 ± 838 vs 3198 ± 640 | 1298 ± 222 vs 1337 ± 143 |
| encode ASCII, 1 KiB | 1075 ± 235 vs 3805 ± 684 | 1137 ± 255 vs 1897 ± 534 |
| decode, 1 KiB | 2517 ± 508 vs 7178 ± 3319 | 1477 ± 405 vs 1575 ± 372 |
| decode ASCII, 1 KiB | 2372 ± 443 vs 8062 ± 2415 | 1412 ± 324 vs 1469 ± 445 |
| encode bytes, 1 MiB | 1126211 ± 531361 vs 3903854 ± 613995 | 848287 ± 177503 vs 968689 ± 150942 |
| encode ASCII, 1 MiB | 989743 ± 218955 vs 4064626 ± 848844 | 956575 ± 170919 vs 1105205 ± 248391 |
| decode, 1 MiB | 2105437 ± 463285 vs 7863885 ± 3255086 | 1140440 ± 295205 vs 1203605 ± 249932 |
| decode ASCII, 1 MiB | 2038678 ± 428275 vs 6564973 ± 2724103 | 1224525 ± 263854 vs 1273040 ± 294262 |

The report decision `KEEP evidence for this case only` means every one of the
seven paired candidate/previous ratios was at least 5% faster; `SKIP` means
that strict consistency criterion was not met, not that the candidate is
necessarily slower. On 3.14.7 this criterion passed for 8 of 12 cases (all
except both decodes at 12 B, decode at 1 KiB, and encode bytes at 1 KiB).
On 3.15.0rc2 it passed only for `b64encode` and `b64encode_ascii` at 12 B and
`b64encode_ascii` at 1 KiB. All other cases were inconclusive under the paired
criterion. Even a KEEP is not a statistical significance test or a general
performance guarantee; broad per-sample variation remains.

The 3.15.0rc2 dispatch cutoffs (bytes encode >=2048 B and decode text >=64
characters) are based on the separate measured crossover probes, not on a
claim that rc2 results generalize to final CPython 3.15, other builds, CPUs or
OpenSSL versions. The crossover inputs are payload sizes 32 and 64 bytes
(encoded text lengths 44 and 88 characters); see [crossover](results/base64-evp315-crossover.txt)
and [large-input evidence](results/base64-evp315-large.txt). The implementation
uses stdlib above those thresholds on 3.15; 3.14 uses EVP for eligible inputs,
and future versions conservatively use stdlib.

Exact recorded commands (run from the repository; set the interpreter and
baseline paths to your local installations):

```bash
P=/path/to/venv314/bin/python
"$P" bench/base64_compare.py --python "$P" \
  --baseline-root /tmp/openssl-baseline/314 \
  --legacy-root /tmp/legacy314/crypto \
  --samples 7 --target-ms 20 --json-output /tmp/evp-final314.json

P=/path/to/venv315/bin/python
"$P" bench/base64_compare.py --python "$P" \
  --baseline-root /tmp/openssl-baseline/315 \
  --legacy-root /tmp/legacy315/crypto \
  --samples 7 --target-ms 20 --json-output /tmp/evp-final315.json
```

The temporary baseline snapshots and prepared legacy trees are not checked in;
raw JSON/text outputs persist, but reproduction requires those exact inputs and
environments to remain available. Runs used fresh processes, paired order and
one warmup; they are Python-call microbenchmarks with GC enabled. Scheduling,
frequency variation and system load create substantial noise. Comparisons are
per workload, and no unqualified speed claim is warranted.

## Final Cython-versus-legacy measurements

Post-removal final results are [3.14.7 crypto](results/cython-final-legacy-cpython314.txt), [3.15.0rc2 crypto](results/cython-final-legacy-cpython315.txt), [3.14.7 protocols](results/cython-final-protocol-cpython314.txt), and [3.15.0rc2 protocols](results/cython-final-protocol-cpython315.txt). These are paired/alternating exploratory comparisons (seven samples, one warmup; crypto and protocol harnesses use the iteration counts recorded in each artifact), not legacy project's historical benchmarks. They include broad, noisy per-sample ranges: individual medians are not stable performance guarantees and do not support a blanket speed claim.

Only matching outputs were timed. Intentional legacy mismatches were reported and excluded: Base64 decoding of terminal NUL (`b"\x00"` becomes `b""` in legacy but is preserved by picore), Ethereum address derivation (legacy includes the SEC `0x04` prefix in its hash), and Bitcoin signed-message hashing (legacy raw single SHA-256 versus picore framed double SHA-256). Legacy Ed25519 is Python big-integer arithmetic, not a constant-time comparison baseline. See the result artifacts for workload-specific results and spreads.

## Cython-controlled comparison

The compiled-extension versus Python/provider comparison used matching installed trees, dependencies and outputs, paired order, seven samples and one warmup per sample. Final artifacts: [CPython 3.14.7](results/cython-final-cpython314.txt) and [CPython 3.15.0rc2](results/cython-final-cpython315.txt) (**3.15.0rc2**, not final 3.15). These report Linux x86-64, Cython 3.3.1, picop 3.13.0, PyCryptodome 3.24.1 and the per-sample paired spreads; both report matching outputs. The compiled extension came from the checkout, and the Python baseline was copied from the same source tree.
The baseline snapshot and interpreter environments were temporary and are not included artifacts. Saved result files preserve the measurements but cannot guarantee independent reproduction after those temporary inputs are removed.

The comparisons show substantial overlap in paired sample ranges and unstable direction; isolated ratios below 1 do not establish repeatable gains. For example, Base64 timings varied by input size and interpreter, Keccak 200-byte was slower compiled on both, and Ed25519 sign/verify results also varied; the wide spreads on crypto operations overlap. No blanket Cython speedup is supported. Cython remains the implementation technology for the modules, not a speed claim.

## Picop helper A/B

`picop_ab.py` builds equivalent Cython candidate arms in one temporary extension and compares the selected direct CPython C-API arms with picop helpers. Production now uses direct arms such as `PyBytes_CheckExact`/`PyBytes_GET_SIZE`, `PyBytes_FromStringAndSize`, `found.add(current)`, and `pending.append(_base_type(field["type"]))`; the script reconstructs picop arms by exact, unique source replacements. This keeps the experiment reproducible without retaining unproven picop substitutions in production. From the repository root, with each interpreter's environment containing Cython, setuptools and picop:

```bash
.venv314/bin/python bench/picop_ab.py --samples 9 --warmups 2 --iterations 50000
.venv315/bin/python bench/picop_ab.py --samples 9 --warmups 2 --iterations 50000
```

Run sequentially. The recorded results are [`3.14.7`](results/picop-ab-cpython314.txt) and [`3.15.0rc2`](results/picop-ab-cpython315.txt), on Intel Core i7-6700 / Linux x86-64, Cython 3.3.1 and picop 3.13.0. Each reports nine paired samples, two warmup pairs and 50,000 iterations per block. Ratios and paired sample values fluctuate around 1; several apparent wins are exceeded by spread or reverse direction across interpreters. Neither interpreter shows a stable picop win meeting the ≥5% threshold used for that experiment. Accordingly, the tested substitutions were removed; there is no claimed picop speedup.

Picop was consulted and remains a runtime dependency as the project's low-level base. Its presence does not imply these measured candidates should be used or that the package is faster.

The historical Base64 ASCII picop A/B case is no longer reconstructible from
the current EVP source and was removed. The remaining A/B cases cover signer
byte length, CompactSize, and EIP helper operations only.

## Full crypto recheck

The audit compared a historical baseline whose fresh sdists produced
separate native wheels passing 416 tests each. Build and install the baseline
and each candidate in separate environments with matching dependencies.
The recorded environment was Linux x86-64, Intel i7-6700, GCC 15.2.0,
Cython 3.3.1, OpenSSL 3.5.5, pybase64 1.5.2, Picop 3.13.0,
PyCryptodome 3.24.1, btclib-secp256k1 0.8.0.16 and eth-account 0.14.0.
The interpreter builds were CPython 3.14.7 and 3.15.0rc2.

Run serially, with each interpreter pair on the same CPU. The warm comparison
below includes a decimal-string chain ID to exercise Agent fallback separately
from canonical Agent and generic Mail/array workloads.

```bash
taskset -c 1 python3.15 bench/eip712_agent_compare.py \
  --baseline-python "$BASELINE_PYTHON" --candidate-python "$CANDIDATE_PYTHON" \
  --samples 9 --iterations 1000 --warmups 2 --paired-block-size 10
taskset -c 1 python3.15 bench/crypto_cold_import.py "$BASELINE_PYTHON" "$LAZY_PYTHON"
taskset -c 1 "$BASELINE_PYTHON" bench/crypto_recheck_alternatives.py
taskset -c 1 "$BASELINE_PYTHON" bench/crypto.py --legacy-root "$LEGACY_ROOT" \
  --samples 7 --iterations 20 --warmups 1 --sizes 12 1533 1536 1539 4096
taskset -c 1 "$BASELINE_PYTHON" bench/crypto_protocols.py --legacy-root "$LEGACY_ROOT"
taskset -c 1 "$BASELINE_PYTHON" bench/picop_ab.py --samples 9 --warmups 2 --iterations 50000
```

### Picop call-site ledger

| Call site | Public helper and access | Decision basis |
|---|---|---|
| Base64 and signer exact byte checks/size; Keccak storage check (`base64.pyx`, `ed25519.pyx`, `secp256k1.pyx`, `keccak.pyx`) | `cybytes.bytes_check_exact`, `bytes_check`, `bytes_len` (Python and Cython) | **SKIP:** same C API on exact/storage paths; do not replace the dynamic `isinstance` or subclass `len()` fallback. |
| CompactSize and fixed Agent output buffers (`bip137.pyx`, `eip712.pyx`) | `cybytes.bytes_from_string_and_size`, `bytes_new`, `bas_string` (Cython, picop 3.13.0) | **SKIP:** same allocation/pointer operations; no reduction in copies or lifetime requirements. |
| Generic EIP-712 dependency traversal (`eip712.pyx::_type_dependencies`) | `cylist.list_append`, `cyset.set_add` (Python and Cython) | Prior measured A/B did not justify production use. Generic caller-supplied mappings/arrays are not exact containers; do not bypass their dispatch. |
| Agent exact-string gates (`eip712.pyx::_try_hash_agent_fixed`) | `cystr.str_check_exact` (Python and Cython) | **UNMEASURED:** no established gate overhead; never apply blindly to validation admitting subclasses. |
| Agent four exact-dict field lookups (`eip712.pyx::_try_hash_agent_fixed`) | `cydict.dict_get_ref` (Cython trial) | **SKIP:** installed-wheel trial passed digest tests but missed the speed gate; missing and `None` must both fall back and lookup errors must propagate. |
| Agent uint256 and secp256k1/BIP-137 scalars | `cylong` conversion helpers (Cython) | **SKIP:** fixed-width helpers stop at 64 bits; no matching public one-shot 32-byte big-endian buffer wrapper in Picop 3.13.0. |
| BIP-137 public-key comparison (`bip137.pyx::bip137_verify_message`) | `cybytes.bytes_eq` (Python and Cython) | **SKIP:** exact-only comparison needs a guard; unconditional use bypasses subclass equality. |
| Python production paths | `picop.hot` and public Python extensions | No Python hot caller: `crypto/__init__.py` only re-exports. Standalone Python `bytes_len` versus `len` is attribution, not a production win. |

The dict trial differs only at the four `.get()` lookups guarded by the exact
dict and exact key checks: `from picop.cydict cimport dict_get_ref`;
`dict_get_ref(<dict>domain, key)` for `name`, `version`, `chainId` and
`verifyingContract`. Its isolated build adds `picop==3.13.0` to
`build-system.requires`, includes the installed package's parent in Cython's
`include_path`, and passes the package directory to the EIP-712 extension's
C `include_dirs`. Production dependency metadata alone does not enable
`cimport`. A mandatory runtime dependency has no current consumer call site
unless a measured candidate is retained or another public dependency contract
justifies it; changing that packaging policy is separate.

The three canonical Agent median paired ratios were 1.011–1.046 on 3.14
and 1.021–1.029 on 3.15 (trial/checkpoint; lower is better). Mean ratios
were 1.020–1.046 and 1.009–1.022, respectively. No workload reached the
required 8 of 9 paired wins of at least 5%.
[3.14 raw dict trial](results/crypto-recheck-picop-3.14.json) ·
[3.15 raw dict trial](results/crypto-recheck-picop-3.15.json).

**BIP-137 signing payload — SKIP.** A separate Cython candidate used picop
3.13.0's `bytes_new` and `bas_string` to fill one 65-byte output instead of
building a header bytes object and concatenating twice. The candidate matched
the installed implementation across all four signature header classes, bytes
subclasses, and rejected inputs. With 13 alternating paired samples of 400
public signing calls per arm, candidate/control median ratios were
1.001–1.030 on CPython 3.14.7 and 0.996–1.014 on 3.15.0rc2. Each workload
won only 4–7 pairs out of 13. The allocation savings did not translate into
a repeatable improvement in signing.

### Cold provider trial

The candidate moves `eth_account.messages` and `eth_utils.keccak` imports
behind generic-envelope validation. Canonical Agent continues to use local
Keccak and does not trigger those imports. Each cold sample uses a fresh
isolated process; startup before `import picore.crypto` is excluded.

| CPython | Eager import | Lazy import | Eager first fallback | Lazy first fallback |
|---|---:|---:|---:|---:|
| 3.14.7 | 1110 ms | 108 ms | 1.33 ms | 896 ms |
| 3.15.0rc2 | 1297 ms | 124 ms | 1.13 ms | 1219 ms |

These are separate medians, not additive stage costs. The provider still
has to load on first generic use and is still an installed dependency.
[3.14 raw cold samples](results/crypto-recheck-cold-3.14.json) ·
[3.15 raw cold samples](results/crypto-recheck-cold-3.15.json).

**KEEP:** warm median paired ratios across the canonical, fallback and
generic workloads ranged from 0.975 to 1.014; each workload had at most one
pair over 1.10. The same-wheel generic controls passed on both ABIs.
[3.14 warm trial](results/crypto-recheck-lazy-3.14.json) ·
[3.15 warm trial](results/crypto-recheck-lazy-3.15.json) ·
same-wheel controls [3.14](results/crypto-recheck-aa-3.14.json) /
[3.15](results/crypto-recheck-aa-3.15.json).

The separate hardening comparison against `24d01ec908` showed canonical
Agent median paired ratios of 1.051–1.074 on 3.14 and 0.993–1.024 on 3.15;
it does not establish a cross-version direction or isolate a cause.
The hardening remains in place.
[3.14 raw hardening comparison](results/crypto-recheck-hardened-3.14.json) ·
[3.15 raw hardening comparison](results/crypto-recheck-hardened-3.15.json).

### Module decisions

All six retained extensions loaded from the installed artifacts, with
**417 tests passing per wheel** outside the checkout. No free-threaded or
non-Linux ABI was verified.

| Module | Replay and decision | Runtime change |
|---|---|---|
| Base64 | Exact strings at 2048 and 2052 encoded characters beat strict binascii on both versions. Bare pybase64 was faster below the cutoff for the one measured payload, but raw provider timings do not cover picore's rejection and subclass contracts. Late invalid inputs still pay for provider rejection and strict fallback. Keep the existing dispatch; evaluate any lower cutoff across the input distribution and malformed cases before changing it. | None |
| Keccak | Independent boundary vectors passed. Historical implementation's 135/136-byte mismatches were excluded. PyCryptodome `digest_bytes=32` versus `digest_bits=256` varied by size and interpreter in the direct attribution arms; no fixed-width keyword or backend change. | None |
| Ed25519 | Accepted vectors and rejection rules passed. Direct pre-imported signing omits key import; direct verification has weaker acceptance. Neither is a replacement. | None |
| secp256k1 | Cross-recovery passed. Direct checked signing with matching output conversion was about 4% faster in attribution, not a like-for-like replacement. Generated C showed Python dispatch for `_private_key` and `_check_bytes`; no evidence that changing those calls alone would clear the gate. | None |
| EIP-712 | Fixed and generic provider outputs matched oracles. One equal-digest legacy Agent replay showed 44.6 vs 39.6 µs on 3.14 and 55.2 vs 75.3 µs on 3.15 (picore vs anonymous legacy baseline); this is not validation parity. Picop dict lookups missed the gate. | Defer the generic provider import |
| BIP-137 | Bitcoin Core fixtures and CompactSize boundaries passed. The precomputed-prefix stdlib arm was faster at small sizes but omits production framing work and did not win at 64 KiB. No change to framing, verification or curve provider. | None |

- Direct alternatives and Python Picop: [3.14](results/crypto-recheck-alternatives-3.14.jsonl), [3.15](results/crypto-recheck-alternatives-3.15.jsonl). Python `picop.hot.bytes_len` was 1.30× and 1.26× the time of builtin `len` in this isolated workload.
- Cython Picop helper replay: [3.14](results/crypto-recheck-picop-helpers-3.14.txt), [3.15](results/crypto-recheck-picop-helpers-3.15.txt). Signer length, CompactSize, and EIP dependency-closure gains did not hold across versions.
- Historical baseline replay: [3.14 codecs/hashes/Ed25519](results/crypto-recheck-legacy-crypto-3.14.txt), [3.15 codecs/hashes/Ed25519](results/crypto-recheck-legacy-crypto-3.15.txt), [3.14 protocols](results/crypto-recheck-legacy-protocols-3.14.txt), [3.15 protocols](results/crypto-recheck-legacy-protocols-3.15.txt).

Retained wheel SHA-256:
`dde0b7dddea8948496cf3e23d8149f53fd050034836b222ae50b00e100f43f42`
(3.14) and
`8429f8c972e0c3b0c4fc925bc0cf6c60656ecc78ac6287afa9644e3f2d869737`
(3.15).

## Native architecture pass

Control: `1e62da248a`. CPython 3.14.7 and 3.15.0rc2, regular GIL builds,
Linux x86-64, OpenSSL 3.5.5. Each candidate compiled as an extension for
its target ABI. Thread CPU time excludes descheduling; nine pairs alternate
order after two warmups. Each row verifies equal outputs before timing.
[All samples and rejection cases](results/crypto-native-design.txt).

| Call site | Input | Trial/control, 3.14 | Trial/control, 3.15 |
|---|---|---:|---:|
| Base64 decode | 32 decoded bytes | 0.536× | 0.486× |
| Keccak-256 | 32 bytes | 0.140× | 0.136× |
| Keccak-256 | 64 KiB | 0.767× | 0.798× |
| EIP-712 Agent | Canonical fixed schema | 0.145× | 0.188× |

- **Base64: keep fused short decode.** One loop checks the alphabet and
  writes the exact result instead of scanning and then calling EVP. Large SIMD
  and version-specific stdlib thresholds are unchanged. Malformed input
  still goes through strict binascii and showed no repeatable timing direction.
- **Keccak and EIP-712: keep the native provider.** Cache the EVP algorithm
  handle, not the digest context or caller input. EIP-712 uses the same
  fast path for all six canonical Agent digests. Generic schema handling and
  fallback stay unchanged. Paired cold crypto import ratios were 0.987× and
  0.944×; raw medians were noisy, so no startup speedup is claimed.
- **BIP-137: keep the current frame.** Segmented EVP avoids copying the message
  but adds digest-context work. A single-allocation frame removes temporaries
  but still copies the payload. Neither won consistently across sizes and
  both ABIs; at 1 MiB their paired ratios were 0.891×/1.027× and
  0.967×/1.042× respectively.
- **Ed25519:** verification must preserve the cofactored acceptance policy,
  not merely pass ordinary signatures. Native private-key derivation and
  signing are separate candidates; not measured in this pass.
- **secp256k1:** a compact-signature bridge could avoid converting to Python
  integers and back in Bitcoin signing and verification. Keep provider checks;
  a direct raw signing call is not equivalent. Bridge not measured here.

Picop's byte buffer/length helpers do the same direct C-API operations used
here; no new picop substitution was identified.

## Async token bucket

Run `python bench/ratelimit.py` with the installed wheel. Optionally pass
`--reference PATH` for a standalone source snapshot or ABI-matched extension
with the same API, or `--legacy-module "$LOCAL_TOKEN_BUCKET_MODULE"` for an
ABI-matched Cython build on `PYTHONPATH`. The older constructor takes positional
`(capacity, refill_amount, refill_period_seconds)`; only shared positive
integral token workloads are compared, not validation or numerical precision.

The harness times batches of 4,096 constructions, 4,096 uncontended unit
acquisitions, and 32 tasks contending for a capacity-one bucket refilled every
millisecond. It checks completion and FIFO before contention timing, excludes
event-loop creation, and takes nine alternating paired samples after two
warmups. Contended time includes task scheduling and timers, not just limiter
overhead.
Use `--cpu-time` to exclude descheduling from construction and uncontended
acquisition timings. Contention always measures elapsed time.

Measured serially on Linux x86-64 (Intel Core i7-6700, pinned to one logical
CPU), with regular GIL-enabled CPython 3.14.7 and 3.15.0rc2, Cython 3.3.1 for
the compiled baseline, and picop 3.13.0. No free-threaded result.

### Python reference and early trials

The initial Python reference passed 448 tests per wheel outside the checkout.
[Raw samples and paired ratios](results/ratelimit.txt).

| CPython | Workload | Lock-only (ms/batch) | Compiled baseline (ms/batch) | Median paired compiled / lock-only |
|---|---|---:|---:|---:|
| 3.14.7 | Construction | 7.179 | 3.000 | 0.423 |
| 3.14.7 | Uncontended | 10.153 | 9.202 | 0.957 |
| 3.14.7 | Contention | 38.429 | 38.657 | 1.077 |
| 3.15.0rc2 | Construction | 7.970 | 3.041 | 0.397 |
| 3.15.0rc2 | Uncontended | 8.557 | 5.751 | 0.940 |
| 3.15.0rc2 | Contention | 38.139 | 40.229 | 1.064 |

The Cython baseline won construction in 8/9 and 9/9 pairs, but uncontended
in only 5/9 on each version. Ratios are medians of paired times, not quotients
of the two batch medians; substantial sample variance makes the smaller
differences inconclusive.

**SKIP lock bypass.** Both versions passed the FIFO and cancellation tests;
uncontended paired time was 0.539× and 0.484× the lock-only reference, winning
9/9 pairs on each. On 3.14 the construction median rose from 8.545 to 11.339
ms/batch (1.327×), above the 5% gate, although the paired median was only
1.031×. The Python bypass did not ship; the Cython implementation below
retests this design with lazy lock allocation.

**SKIP `picop.cylong.long_check_exact`** for constructor settings and acquire
cost. Exact-type behavior matched, but construction won only 4/9 pairs on
3.14 and 7/9 on 3.15 (paired median helper/builtin ratios 1.042 and 0.958).
Uncontended acquire won 4/9 on both. Nine fresh processes per arm timed
`import picore.ratelimit` with `perf_counter_ns` started inside the process:

| CPython | Builtin check (ms median) | Picop check (ms median) | Median paired picop / builtin |
|---|---:|---:|---:|
| 3.14.7 | 84.370 | 93.217 | 1.092 |
| 3.15.0rc2 | 68.955 | 85.203 | 1.191 |

**SKIP `picop.cytime.time_monotonic`** for the refill clock: its public Python
API returns float seconds, not integer nanoseconds.

### Cython implementation

The retained implementation prevalidates the integer settings, uses native
scaled credit when it fits in `uint64_t`, and uses Python integers otherwise.
It allocates the asyncio lock only when an acquisition has to wait.

| CPython | Workload | Cython wheel (ms/batch) | Compiled baseline (ms/batch) | Median paired baseline / wheel |
|---|---|---:|---:|---:|
| 3.14.7 | Construction | 1.286 | 2.569 | 1.700 |
| 3.14.7 | Uncontended | 1.726 | 10.082 | 4.135 |
| 3.14.7 | Contention | 38.494 | 39.752 | 1.033 |
| 3.15.0rc2 | Construction | 1.385 | 2.999 | 2.195 |
| 3.15.0rc2 | Uncontended | 3.035 | 11.241 | 3.754 |
| 3.15.0rc2 | Contention | 41.568 | 38.724 | 0.955 |

The wheel won all nine construction and uncontended pairs on both versions.
Contention measured elapsed time; the wheel won on 3.14 but was slower on
3.15 in this run. The current and compiled baseline APIs matched on the
positive integral cases measured, not on validation policy.
[Raw samples, including the Python reference](results/ratelimit-cython.txt).

**KEEP `picop.cytime.time_monotonic_raw`** for the fixed-width path. The
[CPython C API](https://docs.python.org/3.14/c-api/time.html) specifies
signed 64-bit nanoseconds. Compared with the same extension using the Python
nanosecond clock and timestamp arithmetic, this path used 0.700× and 0.782×
the uncontended CPU time on 3.14 and 3.15, respectively (9/9 and 8/9 pairs).
Construction and timer-dominated contention did not show a repeatable
regression. Compared with the original lock-only Python module, fresh-process
import medians were 81.45 vs 79.01 ms (Cython) on 3.14, and 77.25 vs 77.29 ms
on 3.15.
In a direct cold-import comparison of the two Cython clock variants, native
over Python-clock paired medians were 1.029× and 0.991×.

**SKIP `picop.cylong.long_as_int64`** for settings and costs. The consumer A/B
did not improve on Cython's checked integer cast. `long_check_exact` is a
direct wrapper around the same `PyLong_CheckExact` macro used by the extension;
there is no distinct type-check operation to speed up.

## Bounded byte FIFO

The shipping Cython ring retains immutable `bytes` references in fixed-capacity
slots. Compare the installed API with the equivalent guarded `collections.deque`
implementation (no lock is needed for that baseline's single-producer,
single-consumer contract):

```bash
python3.15 -m pip install 'Cython>=3.3.1,<4' .
python3.15 bench/byte_ring.py --samples 12 --turns 350000 --threaded-items 65536
```

The selection run additionally compared a Cython copying slab
(`bench/_byte_ring_native.pyx`, build with `cythonize -i` in a scratch directory)
and a historical native FIFO built separately for each interpreter. Pass
`--module-dir` and `--backend LABEL:MODULE:CLASS` for each comparison arm.
Historical timings cover only the common byte workload: it requires
power-of-two capacity and tracks drops, and its invalid-input policy differs.

Twelve rotated samples, capacity 1,024, Linux GIL-enabled CPython 3.14.7 and
3.15.0rc2, Intel Core i7-6700, Cython 3.3.1 and picop 3.13.0. Each round trip
does one push and one pop from the same thread; the two-thread workload
includes yielding while full or empty and checking every received item.

| CPython | Payload | Deque round trip (ns) | Historical round trip (ns) | Installed `ByteRing` round trip (ns) |
|---|---:|---:|---:|---:|
| 3.14 | 32 B | 343 | 288 | 143 |
| 3.14 | 4 KiB | 369 | 600 | 121 |
| 3.15 | 32 B | 369 | 217 | 126 |
| 3.15 | 4 KiB | 380 | 608 | 133 |

The installed ring won all 12 paired round-trip samples against the deque
and historical FIFO in every case. Two-thread medians were 243–265 ns/item
versus 430–481 for the deque. At capacity 1,024, construction plus destruction
took 1.7–2.1 µs versus 0.5–0.7 µs for the deque.
[Raw installed-wheel samples](results/byte-ring-installed.json).

Excluding already-owned payloads, tracked empty storage was about 8.2 KiB for
the reference ring vs 0.86 KiB for the deque; full storage was about 8.3 vs
9.2 KiB. In the selection run, the copying slab reserved about 4 MiB upfront
and took about 4× as long as the reference ring per 4 KiB round trip.

Picop audit (Cython call sites; see [raw candidate and constructor samples](results/byte-ring-selection.json)):

- **SKIP** `bytes_check_exact` / `bytes_len` for push and `long_check_exact` /
  `long_as_ssize` for construction. Their matched arm wraps the same CPython
  operations and did not improve both interpreters consistently.
- **SKIP** `list_get` / `list_set_item` for slot reads and writes. This arm
  lost on the 3.15 round trip and did not reliably improve 3.14. Do not use
  slot initialization helpers to overwrite live slots.
- **SKIP** `long_is_positive` / `long_is_negative` for constructor sign
  checks, and `list_new` with `lset` to initialize slots. Constructor runs
  at capacities 1, 16, 1,024 and 65,536 found no consistent win across both
  versions and capacities. In particular, sign checks reached 1.09× baseline
  time at 1,024 on 3.15; explicit allocation and fill reached 1.20× on 3.14.
- **SKIP** atomics and raw-memory picop helpers in the production ring:
  there is no no-GIL access, raw allocation, or payload copy to replace.

## File utilities

From a checkout, install `picoke[files]` for each interpreter, then run
`python -I bench/files.py` in each environment, sequentially. The benchmark
prints seven alternating warm-cache sample pairs after one warm-up per arm.
Median elapsed time and throughput are per complete API call; throughput
counts records/physical lines (bytes for ZIP reads). Each arm is traced
separately for peak Python allocation, not RSS or all native memory. Output
checks, fixture generation, and tracing are outside the timed samples.

Document comparisons use the same providers with 32 and 4,096 ASCII/UTF-8
values. Filtered lines materialize each result, including decoding and
filtering. ZIP uses 32 stored or deflated entries at 256 B or 64 KiB each;
the direct control reads matching entries but does not enforce size limits
or reject duplicate names. Local decisions require six of seven pair wins
and a 5% median gain on each interpreter for the target workload, without
regressing the other representative workload by more than 5% or materially
increasing memory.

Measured on Linux x86-64 (Intel i7-6700; regular-GIL CPython 3.14.7 and
3.15.0rc2), with picop 3.13.0, tomli-w 1.2.0, and PyYAML 6.0.4.
Initial baseline samples: [3.14](results/files-3.14.txt),
[3.15](results/files-3.15.txt). These precede JSONL decoder reuse.
Final installed decoder samples: [3.14](results/files-jsonl-decoder-3.14.txt),
[3.15](results/files-jsonl-decoder-3.15.txt).
Some pairs vary substantially; treat small timing gaps as inconclusive.

| Candidate (trial/control median time) | CPython 3.14 | CPython 3.15 | Decision |
|---|---:|---:|---|
| Reuse a strict stdlib decoder per JSONL iterator, 4,096 UTF-8 records | 0.450× | 0.469× | **KEEP**: the installed change won all seven pairs for both sizes and text profiles on both runtimes, with lower traced peak. |
| 64 KiB JSONL batching, 4,096 UTF-8 records | 1.068× | 1.071× | **SKIP**: slower; peak traced memory more than doubled. The trial flushed complete lines even on serialization or input-iterator failure. |
| Local binding, 4,096 filtered UTF-8 lines | 1.061× | 0.990× | **SKIP**: inconsistent, even though the 3.14 ASCII fixture improved. |
| Single-pass normalization, 4,096 filtered ASCII lines | 0.665× | 0.745× | **SKIP**: only five pair wins on 3.15; unfiltered 3.15 runs regressed. |
| picop `str_endswith` / `str_startswith`, 4,096 filtered ASCII lines | 1.135× | 1.196× | **SKIP**: no repeatable win; no equivalent helper for Unicode whitespace stripping. |
| picop `long_check_exact`, ZIP limit setup | 1.121× | 1.085× | **SKIP**: slower than `type(limit) is int`, including the once-per-call setup. |
| picop `set_add`, `set_contains`, `list_append`, `bytes_len`, ZIP container operations with 32 entries | 1.375× | 1.485× | **SKIP**: the combined isolated trial lost every pair; does not justify a production substitution. |

There is no matching public picop helper for JSON serialization, TOML/YAML
document parsing, or bounded ZIP decompression. No native or mmap backend
was added.

### Same-contract optimization pass

Trials compared the original public calls to one candidate at a time with
the same fixtures. Both CPython lanes were pinned to CPU 4. Keep criteria:
at least 6/7 paired wins and a 5% lower median on the target on both
interpreters; at most 5% slower on guard workloads; no traced-peak or
measured RSS increase beyond the larger of 5% and 64 KiB. The raw logs
include individual paired ratios and the edge-case checks:
[3.14](results/files-opt-candidates-3.14.txt),
[3.15](results/files-opt-candidates-3.15.txt).

- **YAML:** the LibYAML loader and dumper reduced valid-fixture median time
  to 13–14% and 18–22% of the originals, respectively. Both broke parity:
  the loader accepted a trailing tab and rejected an escaped surrogate;
  the dumper escaped literal astral characters and failed on lone
  surrogates. Lazy `safe_load`/`safe_dump` function imports also failed the
  timing gate. The Python safe provider stays in place.
- **JSON:** per-document decoder construction and a shared encoder missed
  the gate. Combining the two writes with `+` or picop `str_concat` added
  about 0.9–1.0 MiB of traced peak on the 1 MiB string guard.
- **JSONL:** reusing the encoder passed the scratch gate on both runtimes.
  Installed writer medians were 28–29% lower for 4,096 UTF-8 records,
  but the candidate won only 5/7 pairs on each ABI, so it was reverted.
  Byte-level BOM checks (`bytes.startswith` and picop
  `bytes_startswith`) did not consistently beat the Unicode check. Using
  picop `str_concat` on top of encoder reuse also missed the gate against
  the same encoder with `+`.
- **Text lines:** hoisting `strip is True` improved the large filtered
  target on both interpreters, but missed the guard thresholds on 3.15.
- **TOML:** explicit UTF-8 decoding plus `loads` and the binary writer
  both missed the timing gate.
- **ZIP:** the single-chunk path preserved EOF, CRC, and size limits, but
  missed the target on both interpreters and the large deflated guard on
  3.15.

No candidate was retained. The original wheels passed 519 tests on each
interpreter, and base installs still read TOML without the optional
providers. The final paired runs compare the same implementation from
the snapshot and installed wheels; median gaps show measurement variation,
not speedups or regressions. Installed-wheel reports also include the
rejected JSONL writer and fresh-process RSS:
[3.14](results/files-opt-installed-3.14.txt),
[3.15](results/files-opt-installed-3.15.txt).

### Calibrated file optimization pass

CPython 3.14.7 and 3.15.0rc2, GIL enabled, Linux x86-64, Intel Core
i7-6700, pinned to CPU 4. Both wheels used picop 3.13.0, PyYAML 6.0.4,
and `tomli-w` 1.2.0. All six scratch trials passed differential checks
before timing.

Each fixture has nine paired samples from three processes. A sample sums
eight alternating block pairs. A/A compares the installed baseline to
the same implementation from a source snapshot. It requires a median
ratio within 0.95–1.05 and at least 7/9 ratios within 0.90–1.10; on failure,
retry once with 16 pairs and retain that count for the trial. To ship,
the target must reach ≤0.95 with ≥8/9 wins on both ABIs, each calibrated
guard must stay ≤1.05, and traced peak and applicable separate-process
RSS must grow by no more than the larger of 5% and 64 KiB.

Scratch results are median paired trial/control ratios, not ratios of
separate medians. Below 1.00 is faster. No trial qualified.

| Trial target | 3.14 ratio (wins) | 3.15 ratio (wins) | Decision |
|---|---:|---:|---|
| YAML read, 4,096 UTF-8 records, 256 KiB raw buffer | 1.021 (2/9) | 0.994 (6/9) | Target misses; small documents add ~128 KiB of traced peak, and one small guard per ABI fails A/A. |
| JSON read, 32 ASCII records, guarded default decoder | 0.970 (7/9) | 0.929 (8/9) | 1 MiB guards take 1.81–2.40× as long; 3.14 target and one A/A guard miss. |
| JSONL write, 4,096 UTF-8 records, one encoder per call | 0.617 (9/9) | 0.664 (9/9) | Empty write is >8% slower on both; 3.15 large ASCII guard fails A/A. |
| Text lines, 4,096 UTF-8 lines, exact-`True` strip fast path | 0.963 (6/9) | 1.002 (3/9) | Both targets miss; 3.14 target fails A/A and 3.15 unfiltered ASCII guard is 1.107×. |
| TOML lazy import, cold JSONL read of 32 UTF-8 records | 0.866 (8/9) | 0.937 (8/9) | All three wheel-vs-source cold fixtures fail A/A on both ABIs; the apparent gain is inconclusive. |
| ZIP read, one 4 MiB stored member, effective byte budget | 1.001 (4/9) | 1.041 (2/9) | Stable A/A on both; target misses on both. |

No new picop substitutions:

- **YAML — SKIP:** `file_from_fd` takes an existing descriptor instead of a
  path; `file_getline` does not replace the provider's live UTF-8 stream.
- **JSON — UNMEASURED:** `str_contains` is available for the discarded
  literal guards; the catalog also reports a loss at 4 KiB. **SKIP**
  `str_concat` on the unchanged writer: the earlier trial raised peak
  memory.
- **JSONL — SKIP:** no JSON encoder helper. The previous `str_concat`
  writer and `bytes_startswith` reader trials did not qualify.
- **Text lines — SKIP:** no equivalent Unicode `strip` helper; the
  previous `str_endswith`/`str_startswith` comparison did not qualify.
- **TOML — SKIP:** no provider helper. `mod_import` is a specialized
  bytes-name import, not a way to avoid import on unrelated calls;
  `mod_import_object` is documented as slower in Python.
- **ZIP — SKIP:** no bounded member stream helper; the catalog reports
  `BytesWriter` losing to `bytes.join` for materialized chunks.
  Prior `long_check_exact` and `bytes_len`/`list_append`/`set_add`/
  `set_contains` trials showed no repeatable two-ABI improvement.

The unchanged installed wheels passed all 519 tests on each ABI. The
base installs read TOML and reported the required optional extra before
touching paths for TOML writes and YAML. No file operation changed.

| Final same-code control target | 3.14 ratio | 3.15 ratio |
|---|---:|---:|
| YAML read | 0.976 | 1.067 |
| JSON read | 1.004 | 1.041 |
| JSONL write | 0.967 | 0.978 |
| Text lines | 0.957 | 1.006 |
| Cold JSONL import and read | 1.004 | 1.022 |
| ZIP read | 0.969 | 0.956 |

These ratios measure variation, not speedups or regressions. The raw
records include A/A attempts, scratch trials, final installed samples,
traced peaks, and separate-process RSS where applicable:
[3.14](results/files-calibrated-3.14.jsonl),
[3.15](results/files-calibrated-3.15.jsonl).
The standard `bench/files.py` also completed all 55 comparisons per ABI:
[3.14](results/files-calibrated-canonical-3.14.txt),
[3.15](results/files-calibrated-canonical-3.15.txt).

### JSONL: defer encoder creation until the first record

Empty inputs still open and truncate the file, but never construct a JSON
encoder. Nonempty writes reuse one bound stdlib encoder per call. A
manual `next()` loop also avoided encoder construction on empty input but
added iterator overhead; a shared encoder would add import-time work and
shared mutable configuration. Neither was adopted. No picop change:
there is no equivalent JSON encoder, and the existing `str_concat`
comparison did not qualify.

The first wall-time pass found a noisy 32-record UTF-8 A/A guard on 3.15.
The follow-up measured thread CPU time and wall time together to separate
scheduling delays from the actual writes. It kept the same fixture
matrix, interleaving and per-ABI gates. All CPU-time A/A controls passed.
The installed-wheel wall-time ratios below are medians of nine paired
ratios, with a value below 1.00 indicating lower elapsed time.

| Write workload | CPython 3.14.7 | CPython 3.15.0rc2 |
|---|---:|---:|
| Empty iterable | 1.028× | 1.002× |
| 4,096 UTF-8 records | 0.645× | 0.667× |

The large target won 9/9 pairs on both ABIs. Traced peaks stayed within
the memory gate. The 3.15 single-record >64 KiB guard initially measured
1.056× with two calls per block; the separately calibrated confirmation
at 32 calls per block measured 0.920× CPU / 0.920× wall. The median over
all 18 installed CPU ratios was 0.982×. Both results are retained in the
raw logs; the isolated short-block miss did not reproduce.

Differential writer checks passed on both fresh wheels, as did the 12
focused tests per ABI. Raw timings, parity, and traced peaks:
[3.14](results/files-jsonl-lazy-encoder-3.14.jsonl),
[3.15](results/files-jsonl-lazy-encoder-3.15.jsonl).

### Historical comparison

A focused scratch build compiled the historical file extensions separately
for both CPython ABIs, using native stdio/mmap for lines, orjson for JSON
decoding, an in-house TOML subset, vendored libyaml for YAML, and libzip for
ZIP. The comparison measures successful calls with matched data, not
equivalent input/error contracts:

- Both JSON writers use stdlib serialization; historical JSON is pretty
  printed while picoke writes compact JSON. Historical JSONL skips blanks
  and batches writes; the measured JSONL fixtures contain no blanks and
  output bytes were checked for equality.
- TOML readers use arrays of tables. Both writers use matched nested
  tables: the historical writer stringifies dicts inside arrays instead
  of round-tripping them. YAML writers were checked for equal parsed values,
  not identical text.
- Line tests include disk reading and list materialization on LF fixtures,
  not cached access. The historical readers have different newline and
  line-length behavior.
- The historical ZIP API takes bytes, so its measured arm includes reading
  the compressed archive and adapting entries to the same result type. Both
  arms select the same entries with limits, but only picoke enforces the
  full preflight and selected-duplicate contract.

Representative medians in milliseconds, shown as historical → picoke.
The JSONL read row uses the final installed decoder samples linked above.
Original matched-input data for all formats:
[3.14](results/files-historical-3.14.txt), [3.15](results/files-historical-3.15.txt).

| Workload | CPython 3.14 | CPython 3.15 |
|---|---:|---:|
| JSON read, 4,096 UTF-8 records | 1.00 → 3.85 | 1.11 → 2.81 |
| JSON write, 4,096 UTF-8 records | 2.68 → 2.05 | 2.40 → 2.96 |
| JSONL read, 4,096 UTF-8 records | 3.63 → 9.54 | 5.12 → 7.62 |
| JSONL write, 4,096 UTF-8 records | 18.14 → 17.43 | 17.90 → 19.96 |
| TOML read, 4,096 UTF-8 tables | 977.05 → 46.39 | 999.20 → 80.40 |
| TOML write, 4,096 UTF-8 nested tables | 5.58 → 29.17 | 5.78 → 33.56 |
| YAML read, 4,096 UTF-8 records | 58.26 → 815.91 | 39.08 → 830.70 |
| YAML write, 4,096 UTF-8 records | 12.66 → 472.51 | 14.40 → 560.17 |
| Filtered lines, 4,096 UTF-8 lines (mmap control) | 1.18 → 2.03 | 1.12 → 2.04 |
| ZIP read, 32 × 64 KiB deflated | 4.52 → 6.19 | 4.78 → 6.26 |

## Structured JSON logging

Run `python -I -B bench/structured_logging.py --calls 256` with an installed
wheel. Results below use CPython 3.14.7 and 3.15.0rc2, GIL enabled, on Linux
x86-64, pinned to CPU 4. The independent reference uses stdlib `logging`,
`datetime`, and compact strict `json` output with the same schema. Formatter
strings and handler sink bytes matched before timing. Enabled measurements
include `Logger.info`, record creation, formatting, encoding to an in-memory
sink, and the sink reset. They do not measure disk or terminal IO.

| Median time per call | CPython 3.14 | CPython 3.15 |
|---|---:|---:|
| Plain record, formatter only | 10.2 µs | 10.3 µs |
| Plain record, enabled handler | 34.1 µs | 34.5 µs |
| Nested fields, formatter only | 22.2 µs | 19.1 µs |
| Nested fields, enabled handler | 44.7 µs | 37.3 µs |
| Exception record, enabled handler | 254.0 µs | 247.8 µs |
| Disabled INFO call | 0.40 µs | 0.21 µs |
| Fresh-process import, construction, and first format | 0.49 ms | 0.75 ms |

Warm measurements use nine samples of eight alternating block pairs,
measured by thread CPU time. Cold results use wall time and exclude both
process startup and loading the benchmark's stdlib dependencies. Same-code
controls for some fixtures varied outside the 0.95–1.05 median or 7/9
within 0.90–1.10 gates. The absolute timings describe this run, not a general
speedup claim.

Picop 3.13.0 review: **SKIP** `str_concat` for the timestamp's `Z` suffix.
At the complete formatter call site, its paired median ratios were
1.094/1.021 on plain events and 1.014/1.028 on nested events
(3.14/3.15; higher is slower). Some controls were noisy; no win was shown.
**UNMEASURED** `str_check` as a mapping-key predicate: its C-level check is
not a direct replacement for `isinstance` across arbitrary objects.
Exact dict/list predicates do not replace the subclass-aware container
checks; no catalog helper replaces strict JSON encoding. No Cython or new
dependency was added.

Complete paired samples, cold times, byte parity and picop probe:
[3.14](results/structured-logging-3.14.json),
[3.15](results/structured-logging-3.15.json).

### Formatter optimization follow-up

Two same-contract scratch candidates were compared to the installed
checkpoint on CPython 3.14.7 and 3.15.0rc2, GIL enabled, pinned to CPU 4.
Each fixture used nine samples of eight alternating blocks, measured with
thread CPU time. Parity covered nested mappings, shared containers, cycles,
invalid keys, non-finite floats and unsupported values. Traced peaks were
also recorded.

| Candidate and target | 3.14 trial/control | 3.15 trial/control | Decision |
|---|---:|---:|---|
| Manual UTC timestamp, plain event | 1.020× (3/9 wins) | 1.048× (2/9 wins) | Skip: no repeatable win; several same-code controls were unstable. |
| Exact `dict`/`list` field specialization, nested event | 0.953× (8/9 wins) | 1.005× (4/9 wins) | Skip: 3.14 same-code control unstable; 3.15 shows no win. |

The exact-container trial retains copying and falls back to the original
path for subclasses and custom mappings. Reusing a mutable encoder across
format calls was not trialed: it would change the timing and lookup of
`json.dumps`/`JSONEncoder` and introduce shared mutable state. No picop
substitution was identified for these candidates; the previously measured
`str_concat` timestamp substitution was not repeated. No production
optimization was retained.

Raw ratios, per-arm times and peaks: [3.14](results/structured-logging-opt-3.14.json),
[3.15](results/structured-logging-opt-3.15.json). These are exploratory
single-process comparisons; unstable A/A results do not establish speedups.

## Bounded background binary delivery

`BackgroundWriter` transfers references to immutable bytes through a bounded
queue to one worker. This avoids copying a payload while it is queued; it does
not guarantee zero-copy encoding, short-write handling, stream buffering, or
kernel I/O. The benchmark checks delivered bytes and separately compares
formatting through ordinary and background logging handlers. The stdlib
`QueueHandler`/`QueueListener` case has different record preparation and
shutdown semantics and is labeled separately.

The Python validation path was screened against picop 3.13.0's public
`bytes_check_exact` and `bytes_len` together. The nine paired microbenchmark
ratios and host details are in
[the screening report](results/background-writer-picop-screen.txt).
The samples were noisy and did not show a reliable gain; no substitution was
retained. This is not an end-to-end performance claim.

A separate pybind11/C++ prototype built for both ABIs but its first 3.14
contract test did not complete within 45 seconds. It was rejected without a
performance comparison; the prototype is not part of the distribution.

The Cython prototype passed the focused writer tests on both ABIs, but was
not retained: neither target control was stable, its win counts did not meet
the gate, and some guard medians exceeded 1.05. All 16 binary same-code
controls on 3.14 and 15 of 16 on 3.15 remained inconclusive after retry.

| CPython | Target candidate / Python median paired ratio | Candidate wins | Retry control within 0.90–1.10 |
|---|---:|---:|---:|
| 3.14.7 | 0.918 | 10 / 18 | 8 / 18 |
| 3.15.0rc2 | 0.967 | 11 / 18 | 6 / 18 |

The target is four producers, 128-byte blocking writes, idle sink. At least
14 of 18 control ratios were required after retry; none of these target
figures establish a win. Raw samples: [3.14](results/background-writer-3.14.json),
[3.15](results/background-writer-3.15.json).

The run used picop 3.13.0, Linux x86-64, an Intel i7-6700 pinned to CPU 4
with the `powersave` governor, 270 prebuilt items per producer, capacity
256, and a nominal 1 µs sleep for slow writes. Each timing block used three
fresh processes per arm. Binary controls used nine pairs and retried with 18
when unstable; memory samples were collected separately. Reproduction of
the candidate arm requires rebuilding the disposable prototype for the ABI:

```bash
PYTHONPATH=/path/to/cython-prototype python3.15 bench/background_writer.py \
  --workload all --processes 3 --pairs 9 --items 270 --capacity 256 \
  --slow-delay 0.000001 --affinity 4 --candidate bg_cython_trial:BackgroundWriter
```

Omit `--candidate` and `PYTHONPATH` to measure the installed writer and
standard-library baselines alone. CPU affinity does not prevent frequency
changes or scheduling noise.

## Framed MessagePack versus JSON logging

Both arms encode the same records and use the actual background handlers and
writer, with an in-memory binary sink. Each sample checks decoded event parity;
timings include writer startup, producer work, drain and close. Separate
encoding-only and reader timings appear in the raw reports. JSON uses
`ensure_ascii=True`; sizes include the JSON newline or the 12-byte binary frame.
These are not filesystem, `fsync`, multi-process append, or durability tests.

| Event | Producers | JSON bytes / record | Binary bytes / record | 3.14 binary / JSON time | 3.15 binary / JSON time |
|---|---:|---:|---:|---:|---:|
| Short | 1 | 161 | 146 | 1.066 | 1.114 |
| Short | 4 | 161 | 146 | 0.926 | 1.002 |
| Nested | 1 | 201 | 175 | 1.122 | 1.354 |
| Nested | 4 | 201 | 175 | 0.975 | 1.089 |
| Unicode | 1 | 298 | 201 | 1.172 | 1.104 |
| Unicode | 4 | 298 | 201 | 1.014 | 0.914 |

Sizes are per-record medians; time ratios are paired end-to-end medians. None
of the six fixtures passed both JSON and binary same-code controls on either
interpreter, so the timing ratios are **inconclusive**, not speedup or
regression claims. The size differences apply only to these fixtures.

Linux x86-64, Intel i7-6700, CPU 4 affinity, `powersave` governor, msgpack
1.2.4, nine alternating paired samples, 400 records per producer, capacity
256. Each observation used a fresh process. The test interpreters were
CPython 3.14.7 and 3.15.0rc2. Raw samples and controls:
[3.14](results/binary-logging-3.14.json),
[3.15](results/binary-logging-3.15.json).

```bash
python3.15 bench/binary_logging.py --samples 9 --items 400 --capacity 256 --affinity 4
```

Picop review: `bytes_concat` is Cython-only and not a Python replacement for
the framing expressions. Python `bytes_len` and `long_check_exact` were
screened against the corresponding builtins; the noisy microbenchmarks did
not establish a win, so neither was retained. Details:
[picop screening](results/binary-logging-picop-screen.txt).

### Reader and framing optimization follow-up

The reader now handles a complete `bytes` read without allocating a chunk
list, while preserving short-read and subclass behavior. It also avoids a
generator for key checks and uses `struct.unpack_from` for frame integers.
Framing now joins all four pieces once rather than repeatedly concatenating
the payload.

| Fixture | 3.14 reader CPU ratio | 3.15 reader CPU ratio |
|---|---:|---:|
| Short, complete reads | 0.900 | 0.881 |
| Nested, complete reads | 0.956 | 0.936 |
| Unicode, complete reads | 0.925 | 0.919 |
| 32 KiB, complete reads | 0.940 | 0.963 |
| Short, seven-byte reads | 0.966 | 0.969 |
| 32 KiB, seven-byte reads | 1.003 | 0.988 |

Ratios are candidate/baseline paired medians; lower is better. These are
single-thread CPU times for complete `iter_binary_records` calls, not
filesystem or logging-handler throughput. Same-code CPU controls had medians
within 0.95–1.05 and at least seven of nine pairs within 0.90–1.10 for the
listed fixtures. Some wall-clock controls were unstable.

For 32-KiB encoding, paired CPU ratios were 0.944 and 0.962 on 3.14 and
3.15 respectively; independent repeats were 0.948 and 0.969. Small-record
CPU ratios ranged from 0.993 to 1.011. These are encoding-only results; the
background-handler wall-clock controls were unstable and do not establish
an end-to-end improvement.

The first reader prototype regressed seven-byte reads, and a no-copy
validation prototype regressed nested records; both were rejected. The
retained reader and join-based framing passed wire-format and error-path
tests on both ABIs. Raw samples, including rejected trials and controls:
[optimization trials](results/binary-logging-opt-trials.json). Linux x86-64,
Intel i7-6700, CPU 4, `powersave`, CPython 3.14.7 / 3.15.0rc2, picop 3.13.0.
Nine paired samples each alternate 30 blocks; the large short-read fixture
uses two records per block, all other fixtures use 20. Reproduce with a
baseline wheel installed and a candidate source file:

```bash
taskset -c 4 python bench/binary_logging_opt.py aa short read
taskset -c 4 python bench/binary_logging_opt.py /path/to/candidate.py short read
```

### Bounded Cython field validation

The binary codec performs structural materialization and numeric validation
in Cython, retaining the two-pass error ordering and custom container
behavior. Cython's optimized type checks are used only for exact built-ins;
subclasses and objects with dynamic `__class__` use Python's `isinstance`
semantics. Python recursion frames are retained for recursion-limit
accounting, and deep traversal falls back to Python before native stack use
can grow without bound. Encoding and frame construction remain Python.
Header parsing also avoids a duplicate magic check; `JSONFormatter` continues
to use its existing Python field materializer.

| Fixture | 3.14 encode CPU ratio | 3.14 reader CPU ratio | 3.15 encode CPU ratio | 3.15 reader CPU ratio |
|---|---:|---:|---:|---:|
| Short | 0.936 | 0.850 | 0.913 | 0.887 |
| Nested | 0.848 | 0.813 | 0.847 | 0.802 |
| Unicode | 0.932 | 0.870 | 0.902 | 0.867 |
| 32 KiB | 0.978 | 0.967 | 0.960 | 0.972 |

Ratios are optimized/pre-Cython paired medians; lower is better. Each
installed-wheel measurement used nine samples of 30 interleaved blocks,
20 records per block, on GIL-enabled CPython 3.14.7 and 3.15.0rc2. Same-code
CPU controls had medians in 0.95–1.05 with at least seven of nine ratios in
0.90–1.10. An independent bounded prototype also favored native validation
on all nine pairs for the nested encode and reader workloads.

With seven-byte partial reads and 32-KiB records, the corresponding CPU
ratios were 1.011 on 3.14 and 1.005 on 3.15; the latter's same-code control
was unstable. These measurements do not establish a partial-read improvement.
Complete background-handler and cold-import wall-clock controls were unstable
on most fixtures, so these results do **not** establish a general throughput
or startup improvement.

An initial unbounded Cython recursion prototype was rejected: it did not
respect Python's recursion limit. An optimized `isinstance` prototype was
also rejected because it changed the behavior of dynamic `__class__` proxies.
The retained implementation switches deep trees and non-exact types back to
Python; tests cover recursion boundaries, cycles beyond the native depth bound,
dynamic proxies and 25,000 nested containers with a raised limit. A separate
compiled parser prototype did not outperform the Python parser and was not
retained.

Picop `dict_new`, `dict_set`, `list_empty`, `list_append`, `dict_check`,
`list_check`, and exact-type checks were tested as Cython variants. They
showed no consistent consumer-workload gain over ordinary Cython containers
and direct CPython checks, so they were not retained. `dict_set` also required
an exact-string guard and a generic fallback for `str` subclasses to preserve
behavior.

Raw timings, controls and rejected candidates:
[native trials](results/binary-logging-native-trials.json). Linux x86-64,
Intel i7-6700, CPU 4 affinity, `powersave`, picop 3.13.0 and msgpack 1.2.4.
The frozen pre-Cython source is retained solely for comparisons:

```bash
taskset -c 4 python bench/binary_logging_opt.py aa nested encode
taskset -c 4 python bench/binary_logging_opt.py bench/baselines/binary_logging_python.py nested encode
```

The runner reports candidate/installed ratios. With the optimized wheel
installed, the second command reports pre-Cython/optimized; invert its
paired median to compare with the table above.

### Exact-float validation follow-up

The Cython validator checks exact built-in floats with C `isfinite` after
conversion to `double`. Non-exact values still use the existing Python
fallback; structural and numeric validation remain separate passes.

The fixture contains a list of 128 finite floats. Ratios compare the
optimized installed wheel against the previous Cython wheel, with nine
paired samples of 30 interleaved blocks and 20 records per block. Lower is
better.

| Runtime | Encode CPU ratio | Reader CPU ratio |
|---|---:|---:|
| CPython 3.14.7 | 0.845 | 0.850 |
| CPython 3.15.0rc2 | 0.841 | 0.853 |

All nine pairs favored the change for these four measurements; the
same-code controls met the 0.95–1.05 median and seven-of-nine 0.90–1.10
criteria. Independent prototype repeats also favored the float-heavy case.
The 3.15 Unicode reader guard measured 1.022 in the installed-wheel run
and 1.027–1.036 in two prototype runs, so a small regression on that
non-float fixture is possible. These measurements do not establish
background-handler throughput gains or free-threaded behavior.

The structural traversal, Python reader and integer-check candidates did
not show consistent wins and were not retained. Picop
`cyfloat.float_as_double_unchecked` was compared against Cython's
`<double>` conversion for exact floats; direct comparisons did not show a
repeatable gain, so it was not retained. One early installed-wheel
comparison accidentally loaded the old extension in both arms; it is
marked invalid in the raw results, and the corrected comparison loads the
two binaries separately.

Linux x86-64, Intel i7-6700, CPU 4 affinity, `powersave`, GIL-enabled
CPython, picop 3.13.0 and msgpack 1.2.4. Raw paired samples, controls and
rejected candidates: [float trials](results/binary-logging-float-trials.json).
The benchmark runner also provides `floats` and `numbers` fixture shapes:

```bash
taskset -c 4 python bench/binary_logging_opt.py aa floats encode
```

### Fragmented reads and wide records

The reader uses a Cython read loop with an exact-`bytes` length fast path.
It retains the first full chunk by identity and otherwise joins the chunks
actually read; it does not preallocate based on the declared frame size.
Bytes subclasses and dynamic type proxies retain Python checks and length
behavior. Exact dictionaries with exact string keys use a compiled key
scan; other records fall back to Python's key validation.

Installed-wheel reader CPU ratios against the preceding implementation:

| Fixture | 3.14 | 3.15 |
|---|---:|---:|
| Short record, seven-byte reads | 0.848 | 0.848 |
| 32 KiB record, seven-byte reads | 0.722 | 0.719 |
| 128 top-level keys, complete reads | 0.833 | 0.855 |

These are the second of two paired runs; each uses nine samples of 30
interleaved blocks. The short and wide fixtures use 20 records per block;
the large short-read fixture uses two. The listed runs had same-code
controls within the 0.95–1.05 median and seven-of-nine 0.90–1.10 criteria.
The first 3.14 large short-read control was unstable, so that run is
inconclusive. Results do not establish background-handler throughput or
free-threaded performance.

The eager `bytearray(size)` prototype was rejected because it could
allocate the full declared record size before a truncated stream reaches
EOF. Native integer conversion and record-mapping prototypes did not show
consistent wins. Picop `dict_next`, `str_check_exact`, `bytes_check_exact`
and `bytes_len` variants were also compared against the retained Cython
paths; direct paired measurements did not show a repeatable advantage.

Linux x86-64, Intel i7-6700, CPU 4 affinity, `powersave`, GIL-enabled
CPython 3.14.7 and 3.15.0rc2, picop 3.13.0, msgpack 1.2.4. Raw samples and
rejected candidates: [reader trials](results/binary-logging-reader-trials.json).
The runner's `wide` shape exercises 128 top-level keys:

```bash
taskset -c 4 python bench/binary_logging_opt.py aa wide read
```
