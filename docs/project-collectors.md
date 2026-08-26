# Project collector and mapping contract

## Scope

Project collection starts only after a successful transactional import. The
import boundary remains the authority for granted-root containment, bounded
reads, redaction and artifact integrity. Collectors do not execute imported
content, contact a provider, use the network or mutate the source project.

Collector success is an engineering validity statement. It does not make a
Git change, log line, metric or configuration value a causal fact.

## Dataset boundary

CSV, TSV and Parquet sources are imported as metadata-only artifacts. Exact
source bytes remain bound by `ProjectItem.content_sha256`, but diagnosis-visible
artifacts contain only:

- format and schema version;
- row and column counts;
- safe column labels;
- Parquet logical types and row-group count when applicable.

Raw rows are never copied into the artifact or file catalog. The import preview
records `dataset_rows_withheld`, and the item binds the transformation through
`dataset.raw_rows_withheld`. Parquet files are inspected from bounded in-memory
bytes; collectors do not resolve external storage or dataset references.

## File catalog

`collect_project_files()` requires an exactly reconciled `ProjectBundle` and
artifact set. It produces one canonical observation per item with source and
artifact hashes, visibility, redaction state and bounded structural summaries.
It never embeds source payloads. Reordering the supplied artifacts cannot change
the collection digest.

Structured configuration summaries expose top-level keys, not values. Log and
text summaries are explicitly untrusted. Withheld content remains local-only.

## Git boundary

`collect_git_state()` accepts the opaque `GrantedProjectRoot` capability and
requires that it identify the exact Git worktree root. It collects only local:

- HEAD commit and attached/detached/unborn state;
- branch name when symbolic HEAD exists;
- staged, unstaged and untracked changed-file metadata;
- rename/copy source paths;
- local Git implementation version.

The collector disables optional locks, interactive credential prompts, system
configuration and filesystem monitoring. It does not fetch, inspect a remote,
checkout, reset, clean, stage, commit, update refs or write the source tree.
Remote URLs, commit messages and diff contents are outside the collection.

## Fail-closed rules

Collection fails when bundle items and artifacts disagree, a dataset metadata
artifact is invalid, the granted root changed, Git output is malformed or the
granted root is not the exact worktree root. Failed collection cannot mint a
snapshot, evidence reference or lineage edge.

Mapping and semantic validation are a separate downstream gate. A file catalog
or Git state alone is not a valid target, metric, run or baseline mapping.
