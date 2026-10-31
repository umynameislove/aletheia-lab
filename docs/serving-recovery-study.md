# Serving continuation and exact-record recovery

This extension checks three separate obligations: which state a request used,
whether the caller authorized that state, and whether an acknowledged record can
be returned after its storage owner exits. Repairing the next prediction does
not recover an uncaptured past request. Equal output also does not establish
state identity.

## Distributed implementation

The scripts in [serving_recovery](../scripts/serving_recovery/) are a portable
engineering extraction of a completed, source-known development experiment.
They do not replace the historical execution source or reopen previous studies.
All generated preparations, HTTP traces, reference records, logs and databases
must stay outside the checkout. The repository distributes authored code and a
small [aggregate result](../configs/evaluation/serving_recovery_observed_summary.json),
not private research records or human annotations. Historical raw-file hashes
identify the retained inputs; the aggregate alone is not a full raw replay kit.

The native experiment is currently qualified on macOS with `lsof`. Other systems
fail before starting a native server. The SQLite process-exit experiment requires
POSIX; its pure projection checks run without a server. These scripts execute
reviewed local code, not downloaded programs or arbitrary repositories. Importing
them does not start a server, make a provider request or run an experiment.

## Native question and caller contract

The public MLServer repository API loads four authored source editions. Each
fits an actual `LinearRegression` on `x=[-1,0,1]`, `y=w*x+1`, then predicts through
that model and adds an imported helper plus a runtime offset. Training points
and source editions are fixtures, not third-party datasets. Both `x=0` and
`x=3` are exercised. Edition B changes the fitted coefficient; editions C and D
also change helper and runtime source.

The declared caller contract is entry cutover with drained maintenance:

- Publishing source does not activate it.
- An explicit activation requires the complete intended edition.
- A request that selected an old immutable instance before load ACK may finish
  on that instance.
- Restart occurs after admitted requests drain. Unload is allowed maintenance;
  no inference is offered during that interval.

This is an experiment-author contract, not a guarantee made by MLServer or an
independently collected operator requirement. The sequence has 16 inference
IDs across three owner processes: initial A, published B, one held A spanning
load B, active B, load C, restart C, no-op load C, unload/load C, load D and
restart D. The runtime has no destructive unload finalizer.

At use, the runtime records source-acquisition hashes, selected generation,
fitted operands and actual output. A separate scorer checks raw float64
coefficient bytes and intercept against a `Fraction` calculation; absolute
numeric tolerance is `1e-10`, relative tolerance zero. Target state additionally
requires exact source hashes. The driver reference is never an input to the
ordinary observation projection. It remains a separate private local output.

The native preparation pins installed MLServer Python files against wheel
RECORD, selected sklearn files, the CLI, the source template and the driver.
This is source identity under a trusted host, not hostile-host attestation or
complete opaque/native-state evidence. Sampled monitoring checks the owned
process tree, memory/disk limits and exact PID/port loopback listeners. A macOS
IPv4-compatible address representation requires binary loopback plus independent
`lsof` confirmation; arbitrary address suffixes are not accepted.

## Exact-record recovery comparison

The second experiment ingests an already captured, allowlisted observation
projection. This admission ACK is separate from native inference and capture.
All three ordinary layouts receive the same records and the same obligation:
return exactly those canonical records, in order, after the writer is killed,
within two seconds of starting a fresh recovery process.

`static_amortized` stores shared state separately; `compact` additionally uses
zlib; `full` appends whole records. SQLite uses WAL, synchronous FULL, fullfsync
and a transaction per record. ACK follows commit. An extra uncommitted record
is present at kill. Five fixed rotation blocks run each layout in a fresh owner.
Recovery reads only its database/WAL, verifies checksums and integrity, and must
report both the uncommitted ID and a never-captured ID missing. It never reads
the original input as a recovery fallback. The parent uses that input only for
independent acceptance after the child has answered.

Logical and allocated DB/WAL/SHM sizes are recorded separately. Timers include
writer initialization, encoding/commit, process launch and recovery decoding;
comparison time is separate. This is an owner-process exit test on one host,
not a host crash, power-loss guarantee, model re-execution, native serving cost
or measurement of manual preparation effort.

## Observed results and limits

On the completed 31 October 2026 development sequence, all 16 outputs matched
actual-state arithmetic. Twelve complied with the authorized target; four
exposed stale imported helpers after public load. Drained restart restored the
declared target; six post-repair/continuation checks passed, including no-op
load, unload/load and the next repair. Complete ordinary history was sufficient;
this does not demonstrate a novel diagnostic or repair algorithm advantage.

The storage comparison recovered 240/240 repeated queries from 16 unique records
within the deadline. All 30 uncommitted/never-captured queries were missing.
Median logical sizes were 292,336 B (static), 271,736 B (compact), and 238,776 B
(full). Median batch recovery times were approximately 50.687, 45.302 and
51.891 ms respectively, with overlapping ranges. Full append was smallest on
this tiny trace; compact is not unconditionally cheaper. Storage layout and WAL
page overhead are plausible contributors, not separately isolated causal effects.

A separate retained large-model cost study showed about 95.1% collector savings,
but about 16.94% after common recovery data was included. That workload used a
different payload and service tier; its clocks cannot be merged with this trace
comparison. Setup and manual engineering were not all measured as service cost.

Qualification requests and a technical repeated main sequence are not additional
independent cases. The historical execution retained both the unsuccessful
terminal inspection and the qualified run after a monitor/shutdown handoff
correction. Scientific N is 16, not the total number of HTTP calls. Packaging
checks of this extraction are engineering verification, not new transfer evidence.

## Local use

Use separate service and driver interpreters if necessary. The service requires
unmodified MLServer 1.7.1 and scikit-learn 1.9.0. The driver requires psutil 7.2.2;
`lsof` is supplied by macOS. Native preparation records actual dependency versions
rather than silently substituting another release. The original service used
NumPy 2.3.5 and SciPy 1.18.1. A new environment is a new execution identity.

Run from the repository root, replacing the two interpreter variables with local
paths. No credential is required, no provider call is made, and the clean child
environment omits credentials and proxy settings. Choose a new external output
location each time. The commands below start and stop only owned local processes.

```bash
research_output="$(mktemp -d)/serving-continuation"
"$driver_python" -B scripts/serving_recovery/prepare_native.py prepare \
  --service-python "$service_python" --output "$research_output"
"$driver_python" -B scripts/serving_recovery/native_sequence.py qualification \
  --root "$research_output"
"$driver_python" -B scripts/serving_recovery/native_sequence.py main \
  --root "$research_output"
"$driver_python" -B scripts/serving_recovery/project_trace.py \
  --input "$research_output/results/main/requests.json" \
  --output "$research_output/observed.json"
"$driver_python" -B scripts/serving_recovery/sqlite_trace.py execute \
  --input "$research_output/observed.json" --output "$research_output/record-recovery"
```

`prepare` never opens sockets. `qualification` must complete with the same source
pins before `main` is admitted. Every execution destination is create-only;
failed/partial attempts remain available. A terminal nonzero return or incomplete
census must not be relabeled as success. Generated outputs can contain local
paths, process identifiers and reference fields: keep them private and inspect
before sharing. No command here authorizes a protected historical or paid rerun.

## Sources

The public [MLServer model repository API](https://docs.seldon.ai/mlserver/examples/model-repository)
and [custom runtime lifecycle](https://docs.seldon.ai/mlserver/user-guide/custom)
ground the operations. Exact installed `registry.py`, model-repository handlers,
settings reload and dataplane selection code ground the observed behavior.
[SQLite WAL](https://www.sqlite.org/wal.html) and
[synchronous settings](https://www.sqlite.org/pragma.html#pragma_synchronous)
define the storage settings; requested pragmas alone do not certify power-loss
behavior on an arbitrary filesystem.
