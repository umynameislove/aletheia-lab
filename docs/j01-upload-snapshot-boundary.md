# Upload source to snapshot: implementation boundary

Confirmed from current code: `product/imports.py` calls `collect_git_state` when
confirming an import. That collector accepts only an exact Git worktree root.
`ProjectSnapshot` requires a `git_state` collector binding and Git-state hash.
Consequently a ZIP source stored by the hosted source repository cannot be
confirmed by passing its extracted directory to the unchanged local facade.

The hosted implementation must represent missing Git provenance explicitly:

1. Keep current local Git snapshots and their identity rules compatible.
2. Introduce a versioned upload provenance record with workspace/source ID,
   archive SHA-256 and canonical per-file manifest. Git history is not supplied.
3. Mint an upload snapshot only after server-side validation of the selected
   mapping. Bind snapshot identity to the immutable source and mapping, without
   server staging paths or capture time in semantic identity.
4. Persist source/snapshot/workspace association and receipt atomically. A stale
   mapping, missing source, revoked membership or invalid session denies commit.
5. Extend the domain analysis entry point to consume this snapshot provenance,
   retaining the existing observation-versus-cause limits. Do not use a demo
   result or synthesize a Git commit to satisfy the local snapshot validator.
6. Run confirmation and analysis in supervised worker processes with bounded
   resources. Recheck access before publishing the result and on every read.

An initial internal envelope is implemented in `hosted/upload_snapshot.py`,
with explicit `not_supplied` Git provenance and sealed P3 mapping facts.
The source repository can publish/read that envelope after checking workspace
access and matching source SHA-256; tests computed mapping facts from synthetic
files and stored/reloaded the snapshot in isolated PostgreSQL. This does not
implement the trusted worker that must re-read the exact uploaded bytes, enforce
mapping selection and prove the mapping/source relationship before publication.
It is not an approved wire-contract migration. HTTP upload has only a draft
source receipt. Preview/confirm, durable job/result publication and frontend
integration remain unimplemented. J-01 cannot be signed off from this envelope.
