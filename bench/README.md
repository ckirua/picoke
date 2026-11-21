# Crypto performance comparisons

These measurements used a focused historical Cython baseline with minimal package initializers and build configuration, not a full distribution. Reproducing that build requires a compatible source archive and license, a C compiler, OpenSSL headers/libcrypto, Cython, and picop. The build tool requires a fresh output directory and preserves the source license.

From the repository root, install picore, Cython and setuptools separately under each interpreter, then:

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
