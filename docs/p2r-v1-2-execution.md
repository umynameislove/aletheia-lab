# P2R v1.2 registered execution

## Purpose

P2R v1.2 is the prospective methodological successor to the retired v1.1
attempt. It addresses one disclosed construct-validity defect: the registered
replication target could not support the declared 0.20 intervention for both
mechanisms with the prespecified 0.05 reserve.

The amendment does not reinterpret the v1.1 outcome and does not erase either
technical failure. It selects `OperatingSystems` for the Online Shoppers
replication partition by the already published, outcome-blind maximin-capacity
rule. The primary Default dataset retains `EDUCATION`.

## Identity chain

Execution is admitted only when all of the following reconcile:

1. both v1.2 amendment JSON files validate against their complete predecessor
   protocol, recovery, failure-audit, and feasibility chain;
2. both annotated tags reproduce the tracked amendment files exactly;
3. both GitHub releases are immutable, non-draft, and non-prerelease;
4. current dataset archives reproduce the SHA-pinned manifest and receipt;
5. a deterministic compiler produces an executable protocol from each
   amendment and predecessor;
6. each registration binds the amendment hash, compiled execution hash,
   archive-readiness hash, tag, release, and tagged commit;
7. both registrations cover the two mechanisms exactly once.

The compiler permits only the registered target-feature delta. Dataset roles,
split and sealed membership, preprocessing, model, seeds, intervention dose,
nuisance semantics, estimand, thresholds, exclusions, and dispositions remain
inherited.

## Attempt boundary

Preflight does not fit a model, inspect predictive outcomes, or consume the
single registered attempt. It checks immutable evidence and writes two
idempotent receipts:

```bash
PYTHONPATH=src python scripts/p2r_v1_2_confirmatory.py preflight
```

Execution is allowed only from a clean `main` synchronized with `origin/main`.
The operator must copy the exact amendment and registration hashes printed by
preflight into both confirmation arguments. Before loading either registered
dataset, the runtime writes one exclusive paired sealed-open marker. Once that
marker exists, another attempt is forbidden.

```bash
caffeinate -dimsu env PYTHONPATH=src \
  python scripts/p2r_v1_2_confirmatory.py execute \
  --confirm-protocol-sha256s <drift-amendment-sha>,<preprocessing-amendment-sha> \
  --confirm-registration-sha256s <drift-registration-sha>,<preprocessing-registration-sha>
```

Do not run `execute` while developing or reviewing this runtime. Merge the
runtime, require green CI, synchronize `main`, run preflight once, review its
hashes, and only then consume the registered attempt.

## Census and closeout

The complete scientific census is fixed at:

- two mechanisms: data drift and preprocessing mismatch;
- two named dataset roles: primary and external replication;
- five registered seeds per mechanism and dataset;
- 20 dataset-seed measurements;
- 10 conservative paired cross-dataset observations;
- one joint instrument audit and one atomic terminal store.

The runtime releases the two mechanism outcomes together. It cannot publish a
partial scientific result. A successful closeout can produce `admitted`,
`assumption_limited`, or `rejected` per mechanism according to the inherited
rules. An exception after the attempt marker produces only a structured
`technical_failure` store with the exception class and hashed message; partial
measurements and scientific dispositions are withheld.

The atomic terminal store embeds a content-addressed copy of the sealed-open
marker. The attempt boundary, execution commit, compiled protocols,
registrations, and terminal artifact therefore form one verifiable provenance
chain for both complete and technical-failure outcomes.

## Interpretation

V1.2 is still bounded to the two named, previously opened dataset partitions;
it is not an independent new-dataset replication. A pass can support admission
for the corresponding mechanism within this registered scope. A negative or
assumption-limited result remains scientifically valid. A technical failure
does not support a scientific conclusion and must not be repaired by silently
rerunning the registered attempt.

## Verification

After a terminal store exists:

```bash
PYTHONPATH=src python scripts/p2r_v1_2_confirmatory.py verify
```

Verification rejects missing, extra, replayed, wrong-size, or hash-mismatched
artifacts and supports both the original v1 registration schema and the v1.2
amendment registration schema without weakening either identity contract.

## Completed registered result

The single paired attempt completed with no technical failure. Both mechanisms
achieved the declared 0.20 manipulation magnitude, but both failed the
prespecified target-effect, direction, and paired-instrument admission chain.
Data drift and preprocessing mismatch are therefore `rejected`; `n_admitted =
0`. The terminal store is immutable and must not be rerun.

See [P2R v1.2 terminal results and preservation](p2r-v1-2-results.md) for the
complete bounded interpretation, compact summary, external preservation path,
and downstream denominator consequences.
