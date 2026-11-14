# K-04 local producer extension

Synthetic fixtures only; no deployment or joint consumer acceptance is implied. Models come from the maintained workspace/Job producer definitions. Canonical result/project/snapshot IDs are frozen from a valid synthetic domain journey and are never rewritten for these examples.

All page continuations are opaque signed values; current authorization is rechecked on every request. Examples with null continuation represent terminal pages and unknown totals. Invitation creation contains a nonfunctional synthetic token placeholder; pending-list responses never return a token or digest. Real invitation tokens travel only in creation responses and acceptance JSON, never URLs.

Export POST returns 201 with a deterministic descriptor, not a queued JobReceipt. Download GET/HEAD supports 200/206 and one Range; server authorizes before 416/size headers and before each new chunk. Bytes validly delivered before revoke cannot be recalled; a later denied chunk aborts the response. Events are finite paged JSON, not SSE. PDF and physical purge are not supported by this slice. Project deletion creates a retained logical tombstone; canonical rows, object references and receipts remain.
