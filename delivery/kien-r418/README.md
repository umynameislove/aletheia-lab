# Product API — source, verification and handoff

This directory retains the work supporting tasks K01–K18 at the R4.18 checkpoint and the final peer UI response. It is a technical handoff, not the final acceptance report for Bảo.

## Maintained source

The repository root contains the complete maintained source, frontend, tests, fixtures, scripts, contracts and deployment configuration. Its 2,557 original files reproduce local integration snapshot `cf9738b1accef69e6f1402fb241bad17fcc3a477`. The backend runtime identity is `a046bccd9a0beb135951a0928c7ddd131b327302bcacf10c7ddb979757c9d495` (676 files). Final peer UI identity is `5515bc0e3d7e9cfd3be1c302dd3228bfbbf970bb6e7971675707538f21510bb7` (180 files).

The `product-api` publication starts from upstream main `2bbcdc2a7637c840babd8b71910306c64072bc4b`. It reproduces the final source through new functional commits rather than importing the previous large local snapshot commits. Every new commit is limited to 3,000 total added plus deleted lines and is pushed individually in order. Local original branches, WIP snapshots and full integration bundles remain preserved separately. No push to main is part of this operation.

## Supporting material

- `requirements/`: copies of the supplied personal and coordination task documents. Their statuses are historical; they are not rewritten as current acceptance.
- `evidence/`: selected original result receipts, test reports, measurements, failures and source manifests. Files ending in `.gz` are lossless gzip copies of large raw reports; decompress them to recover the original bytes before checking referenced hashes. Receipts retain their original local paths and timestamps.
- `handoff/k0101-r418-integration/`: branch/WIP preservation results and the prepared peer message, recording the earlier local SHA.
- `handoff/k16-r418-image/`: the audited Wolfi Dockerfiles, probes, verification instructions, image checksum and local security result. These supplement the original root deployment configuration, which remains unchanged. The image archive is retained locally and is not a Git source file.
- `tools/`, `tests/`, `installed_wheel_check.py`, `test-constraints.txt`: original verification tools from the Windows working kit. Historical tools expect the original kit layout, including `Release-*`, local paths and external services. They are retained for traceability; do not interpret copying them here as a new portable environment or a completed rerun. Maintained project commands live in the repository root and `scripts/`.

Private data, credentials, seeded actor files, database volumes, node_modules, virtual environments, Docker image archives, duplicate source ZIPs and private WIP bundles are retained locally rather than published in Git. The image archive is 232,465,920 bytes; its SHA-256 and build instructions are included in the security handoff.

## Result scope

Existing sealed-backend unit run: 2,969 pass, 5 deselected. Existing final peer UI local run: 358/358 pass. Source hashes bind these historical receipts to the published runtime/UI; publishing does not create a fresh test run or CI result.

The completed local image security gate, local Git/WIP gate and backend refactor checks do not close joint deployment, IdP, staging, independent restore or primary 50k acceptance. Failed/limited PDF and load observations remain in the receipts. The final acceptance report for Bảo has not been issued.

Existing CI workflows filter pushes to main and also support their declared PR/manual triggers. Pushing this feature branch alone must not be reported as passing CI. Use the workflow's supported trigger on the final branch when coordinating review; this publication never merges main.
