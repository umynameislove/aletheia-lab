# Authorized immutable report exports

`POST /v1/workspaces/{workspace}/results/{result}/exports` accepts
`{"format":"json"}`, `markdown`, or `pdf`. The existing response remains
`201 ExportDescriptor`, followed by authenticated GET/HEAD of `download_url`.
There is no new export-job acknowledgement or bearer download URL. Clients must
offer only formats in current capabilities. JSON and Markdown retain their
existing wire shape; PDF is an additive draft-contract format.

The same actor/workspace request key replays the same artifact and original
expiry. Reusing a key with a different result or format is a conflict. A replay
does not render again or extend expiry. Rendering uses the pinned result, never
the project's newest snapshot. An authorized stored artifact stays downloadable
when its renderer is unavailable. New PDF publication requires both the actual
renderer and the v11 PDF schema; partial or legacy installations do not advertise
PDF. Migrations v1-v10 are unchanged.

## PDF content and installation

Install `.[hosted]` for the service, or `.[pdf]` for local PDF export. Both pin
ReportLab 5.1.0. Missing PDF dependencies return a typed refusal; there is no
ASCII substitute. JSON contains the complete canonical view; Markdown and PDF
share one report projection, including metric operands, class/unit/origin,
runtime, uncertainty, turns, claims, citations, source hashes and limitations.
Numbers use the stored JSON precision without display rounding.

The package contains the original Noto Sans Mono Regular binary and SIL OFL 1.1
license. `product/fonts/provenance.json` records its pinned official upstream
commit and binary/license hashes. PDFs embed a font subset and ToUnicode map.
Vietnamese NFC/NFD displays and copies as NFC; JSON/Markdown retain the original
stored text. Text outside the bundled font's character coverage is refused with
`400 invalid_request`; JSON/Markdown remain available. No glyph is silently
replaced. Formatting escapes are decoded as literal text, not HTML.

ReportLab receives plain text only. Project URLs/images/scripts are never
interpreted or fetched. No JavaScript, attachment, automatic action or remote
image is added to the PDF. Output bytes are deterministic for one view and the
pinned renderer/font version.

## Resource and process boundaries

The API submits reports to an isolated one-shot child through the existing
bounded supervisor, normally one render at a time. The child uses the same
Python interpreter path, preserving its virtual environment. Only the existing
minimal OS environment is inherited, not application/provider/database secrets.
The child fixes BLAS thread counts to one before importing NumPy or product code.

Input is bounded at 16 MiB and output at 32 MiB. The supervisor continuously
drains bounded stdout and stderr, terminates/reaps an oversized or timed-out
child, and holds its slot until cleanup finishes. Parent cancellation also reaps
the child. The report process denies Python network and subprocess operations.
On POSIX, CPU is at most 15/16 seconds soft/hard, open descriptors at most 64,
and file output at most 32 MiB; lower inherited limits are preserved. Linux
also limits address space to 1 GiB. macOS does not provide an enforced RSS cap
through this policy; byte limits, bounded pipes, slots and the wall deadline
still apply. This is a trusted text renderer, not an arbitrary-code sandbox.

## Delivery and authorization

Files are staged, verified and referenced through the canonical object store.
Publication rechecks current authority after rendering, inside its short
transaction. Export handles have a fixed 15-minute expiry and are bound to the
creator, workspace, result and its current project/source export permission.
Neither expiry nor size/range errors bypass authorization. Logical deletion,
grant loss or session/membership loss prevents new delivery. Already delivered
bytes cannot be recalled.

Downloads verify the stored size/hash, publish the correct MIME, safe generated
filename, Content-Length, ETag, `no-store`, and `nosniff`, and support one byte
range. Each additional 64 KiB chunk rechecks current permission and expiry.
No database transaction remains open while awaiting a client write. Each send
has a deadline of at most one second, with the overall configured request
deadline as an additional bound. Failure after headers aborts the incomplete
response; it never sends a second success/error status or a false final chunk.
Clients can retry the authenticated handle/range while it remains authorized.
Each API process admits at most four downloads, holding the permit through
verification and delivery. A fifth returns `503 service_unavailable` without
loading another artifact. Slow-reader buffers are bounded independently of
metadata reads; cancellation, refusal and timeout all release their permits.
This limit is per process, not a distributed or 50k-user capacity guarantee.

## Verification scope

`tests/unit/test_k12_pdf.py` checks real embedded-font extraction, NFC/NFD,
literal malicious links/images, long tokens, tiny/zero/negative operands,
v2 isolated workers, no-network/process policy and safe unsupported glyphs.
`test_k12_worker_bounds.py` uses real noisy children and checks termination,
reaping and slot recovery. Existing report/export tests still check deterministic
bytes, provenance, role/scope, corruption, expiry and revoke between chunks.

Opt-in `tests/integration/test_k12_exports_postgres.py` uses owned UUID PostgreSQL
schemas and actual restricted API logins. It tests all three formats through
publication/download, range/HEAD, replay, expiry and revoke. A separate verified
native HTTPS transport witness uses an explicitly padded valid JSON report to
exercise an 8 MiB download, a stalled TLS reader, concurrent result reads with
one database connection, incomplete-response handling and range retry. It records
first-byte/last-byte/throughput from received bytes. This distribution fixture is
not a claim that an 8 MiB PDF or a 100k-row analysis was rendered. No IdP login,
browser consumer, staging deployment or 50k-user capacity is inferred from it.
