# Ordered product API publication

Branch: `product-api` in `umynameislove/aletheia-lab`. Main was never a push destination.

Upstream base: `2bbcdc2a7637c840babd8b71910306c64072bc4b`.
Final source/support commit: `92507a1e76dc981dc0c61ed74196b36fb73a536f`.
Original verified local integration snapshot: `cf9738b1accef69e6f1402fb241bad17fcc3a477`.

The publication contains 128 source/support commits, each committed and pushed separately before creating the next commit. A final documentation commit adds this report and the commit ledger. Maximum measured source/support diff: 2800 added plus deleted lines; the final documentation commit is checked against the same 3,000-line limit before push.

`PUBLICATION_COMMITS.csv` records every source/support SHA, parent, functional subject, line counts and commit record time. Each row was retained only after its push and remote SHA verification succeeded. Group identifiers organize the publication; they are not requirement coverage or acceptance claims. Subjects name functionality rather than K identifiers. Four large historical OpenAPI fixtures were introduced through valid JSON snapshots containing successive paths and component definitions. Their final bytes match the verified source exactly.

All 2,557 original source files match the verified integration snapshot. The additional 210 delivery files retain requirements, test tools, handoff material and original result receipts; the final two publication documents are additional metadata.

Backend: 676 files, runtime identity `a046bccd9a0beb135951a0928c7ddd131b327302bcacf10c7ddb979757c9d495`.
Peer UI: 180 files, UI identity `5515bc0e3d7e9cfd3be1c302dd3228bfbbf970bb6e7971675707538f21510bb7`.
Source hashes were verified after the sequential pushes. No passed runtime/UI logic was edited for publication, and full functional suites were not rerun. Historical tests remain explicitly historical. The feature branch push does not by itself qualify full CI.

Main remote SHA observed after source publication: `2bbcdc2a7637c840babd8b71910306c64072bc4b` (unchanged from the base). Original local worktrees, WIP snapshots and integration history remain preserved. No force push, main merge or main push was used.

Private credentials, actor seed files, database volumes, caches, virtual environments, private WIP bundles, duplicate source ZIPs and the 232 MB Docker image archive remain local. Image Dockerfiles, checksum, probes and security receipts are included in the handoff. Large raw receipts use lossless `.gz` copies where documented.

This is a publication record, not the final K01–K18 acceptance report for Bảo. Remaining joint review, staging/IdP, deployment and load/restore acceptance keep their original status.
