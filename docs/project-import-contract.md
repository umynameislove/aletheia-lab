# Local project import contract

## Scope

The local project importer converts one explicitly granted directory into a
content-addressed `ProjectBundle`. Imported files are untrusted input. The
importer does not execute project code, run notebook cells, invoke a shell,
contact a provider, open a network connection, or write into the source tree.

This boundary establishes filesystem and content-handling validity only. A
successfully imported bundle is not automatically evidence, a causal fact, a
scientific result, or permission to transmit content to an external provider.

## Authorization

`grant_project_root()` accepts an absolute directory path and returns a local
`GrantedProjectRoot` capability. The capability binds:

- the resolved root identity;
- its filesystem device and inode identity;
- a root fingerprint that can be stored without publishing the host path;
- the derived project identity.

The absolute path is private transient state. It does not appear in the
preview, issues, report, `ProjectItem`, or `ProjectBundle`.

Root symlinks and reparse points are rejected. The importer revalidates the
capability before discovery and again before bundle assembly.

## Default-deny policy

`ProjectImportPolicy` is immutable, strict and content-addressed. Its digest is
bound to `ProjectBundle.permission_policy_sha256`.

The default policy:

- admits only audited UTF-8 text profiles;
- excludes unsupported file types;
- excludes hidden paths and known dependency, VCS and cache directories;
- blocks symlinks, reparse points, hardlinks, special files and containment
  failures;
- bounds discovered entries, admitted items, path depth, item bytes, total
  bytes and line bytes;
- requires secret and PII scanning;
- fixes execution and network modes to `disabled`;
- fixes source mutation to `forbidden`;
- fixes unsafe-path handling to `block`.

Scanner, execution, network and mutation safety fields cannot be disabled by a
project or downgraded by a caller-supplied policy.

## Discovery and read boundary

Discovery is deterministic and uses canonical NFC POSIX-relative identities.
Filesystem-specific spellings remain private. Canonical path collisions are a
blocking error.

Every candidate is inspected without following links. Regular files are opened
read-only with `O_NOFOLLOW` and `O_CLOEXEC` when the platform provides them.
Directory enumeration supplies names only; discovery identity is captured with
`Path.lstat()`. This avoids the zero device/inode values returned by
`DirEntry.stat()` on CPython 3.11 for Windows while retaining the same
path-to-descriptor identity comparison on every platform.
The importer compares filesystem identity, mode, size and nanosecond mtime:

1. at discovery;
2. immediately after open;
3. after the bounded read;
4. through the path after the read;
5. once more before assembly.

Directory membership observations are also rechecked before assembly. The
recheck compares both directory identity metadata and a lossless snapshot of
entry names because Windows does not guarantee an immediate directory-mtime
change for membership mutations. A race, replacement, removal, or membership
change therefore aborts the transaction on every supported platform.

## Content handling

Allowed input must be strict UTF-8 without unsafe control characters or lines
above the configured bound. JSON, notebooks, YAML, TOML, CSV and TSV receive
additional non-executing structural validation.

The structured validators reject:

- malformed syntax;
- ambiguous duplicate keys or duplicate tabular headers;
- non-finite numbers;
- ragged tabular rows;
- notebooks without a cell list;
- unsafe YAML constructors through `SafeLoader`.

Python, Markdown, logs and other admitted text are retained only as untrusted
text. They are never imported or executed. Instruction-like text receives a
structured warning and grants no policy, role, or tool authority.

High-risk credential-like content is represented by a local-only withheld
item. Its diagnosis artifact is a deterministic placeholder. PII is replaced
before an artifact becomes diagnosis-visible. Redacted and withheld artifacts
use `text/plain` so the system does not claim that rewritten bytes preserve the
source file's structured media type.

## Structured issues and preview

Every issue code has one fixed severity, stage and public message. Constructing
the same code with a weaker severity or different meaning is invalid.

`ProjectImportPreview` reconciles canonical decisions for included, excluded,
redacted, withheld and blocked paths. It binds all decisions and issues into
`preview_sha256`. Human-readable messages do not include raw secrets, unsafe
path spellings, host paths or operating-system exception text.

## Atomic outcomes

`import_local_project()` has three terminal statuses:

- `imported`: all admitted items are diagnosis-safe and no restriction issue
  was emitted;
- `imported_with_restrictions`: the bundle is valid, but one or more items were
  redacted, withheld, or marked as untrusted instruction text;
- `blocked`: at least one blocking issue exists.

A blocked transaction returns a preview and report digest, but returns no
`ProjectBundle` and no artifact payloads. It cannot mint snapshot or evidence
references. Valid files processed before a later blocker remain preview
decisions only; no partial artifact set escapes the transaction.

For successful transactions, bundle items, artifact payloads, manifest entries,
collector inventory, policy hash, validation summary and ingestion report must
reconcile exactly.

## Trust boundary limitations

The importer protects against untrusted project content and filesystem races.
It is not an operating-system sandbox against a malicious Python caller that
already controls the Aletheia process. Provider authorization, persistence,
evidence projection, semantic mapping, causal admission and deletion/retention
execution are separate downstream boundaries.
