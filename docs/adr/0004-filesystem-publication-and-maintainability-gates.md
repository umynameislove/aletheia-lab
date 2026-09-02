# ADR 0004: Filesystem publication and maintainability gates

- Status: accepted
- Date: 2026-09-02

## Context

Several independently developed stores implemented the same immutable-file
publication sequence: create a sibling stage, flush and `fsync` it, publish it
without replacement, reconcile a concurrent destination, remove the stage, and
verify final bytes. Small platform differences in those copies had become an
experimental risk. The repository also had no blocking constraint preventing
new complexity violations, unreviewed oversized modules, or another copy of the
publication algorithm.

## Decision

`aletheia_lab.filesystem` owns two distinct publication semantics:

1. `publish_immutable_file` provides create-only, byte-idempotent publication
   for immutable files. It never replaces different bytes. It uses a same-parent
   hard link, bounded Windows retries for transient access denial, deterministic
   conflict handling, stage cleanup, and final-byte verification.
2. `publish_staged_directory` publishes a complete same-parent directory. It
   has no copy fallback and never replaces an existing destination.

The attempt store, development failure receipts, dataset manifests, and project
content-addressed objects delegate immutable file creation to the shared core.
Overwrite-oriented APIs, downloads, and unpublished staging writes remain
separate because their semantics are not equivalent.
Unpublished directory trees are flushed bottom-up where directory handles are
supported; publication itself has no post-commit operation that could report
failure after the final path has become visible.

The repository enforces a versioned maintainability budget:

- the existing C901 count and maximum score cannot increase, either globally or
  within a package;
- a production module over 800 lines requires a named, bounded rationale, and an
  exempt module cannot grow beyond its frozen budget;
- direct SHA-256 call counts cannot increase by package;
- `os.link` is owned only by the filesystem publication core;
- every `_atomic_create` compatibility method is an enumerated delegate rather
  than an independent implementation.

Three execution profiles separate feedback purpose without changing test
semantics: a fast architecture/contract profile, the evaluation profile, and a
Windows filesystem-publication profile. The complete suite remains blocking on
Python 3.11 and 3.12; coverage remains blocking once on Python 3.11.

## Consequences

Publication fixes now have one implementation and one cross-platform regression
surface. Reader and verifier layers retain no write authority. Existing public
schemas, canonical bytes, identities, receipts, and terminal-state semantics do
not change.

The complexity budget is a ceiling, not a quality claim. It blocks regression
but does not certify that the existing baseline is ideal. Reducing a budget is
allowed when code is simplified; increasing one requires an explicit review of
this decision and its machine-readable configuration.

## Rejected alternatives

- Replacing immutable files with `os.replace` was rejected because it can
  overwrite a concurrent writer's different bytes.
- Treating all temporary-file writes as identical was rejected because download
  replacement and unpublished staging have different contracts.
- Enabling C901 as an immediate zero-violation gate was rejected because it
  would force unrelated changes to frozen registered research code. A
  non-increasing baseline gives new code a hard constraint without rewriting
  historical implementations.
