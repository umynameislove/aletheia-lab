# Diagnosis main technical recovery

The original registered run remains immutable. It terminalized all 1,024
logical requests: 128 deterministic B0 completions and 896 provider-backed
technical failures. The passing synthetic schema smoke demonstrates one final
diagnosis turn only. It does not establish that the retrieval-selection turns,
the 896-request recovery, or downstream relation scoring will succeed.

The forward recovery uses the same frozen census, model snapshot, prompts,
sampling, route order, output-token limit, local response schema and scientific
analysis plan. It projects only the final diagnosis schema for provider
transport. Retrieval-selection turns retain their original schema. The only
permitted local correction is the previously declared boundary-whitespace
trim on fields that fail the original regex. If applied, the original provider
bytes and a hash-linked record stay in the private recovery directory; the
corrected bytes must pass the original schema and the existing route-specific
semantic check. No claim meaning, citation set, label, or internal whitespace
may be altered.

`diagnosis_main_technical_recovery.py prepare` checks the complete predecessor
terminal ledger, passing smoke, private census seal, frozen contracts, clean
synchronized `main`, and an operator cost ceiling. It writes a private,
self-hashed plan but makes no provider call. `execute` requires that exact plan
hash and an API credential. It creates a one-use lease before any provider
attempt. It first executes one prospectively selected request from each of the
seven provider-backed routes. All seven must finish with a valid logical
output before the remaining requests are attempted. These seven are part of
the 896 recovery requests, not extra calls. A failed pilot stops the recovery
and records its technical status without changing the protocol. A partial or
interrupted execution is preserved; invoking `execute`
again is refused rather than silently replaying a potentially charged call.
`verify` is read-only and checks the complete recovered ledger, the unchanged
predecessor, B0 identity, and compatibility with the frozen
scoring-preparation contract. For every local trim it also replays the repair
from the preserved provider bytes and matches the accepted bytes to the stored
turn result. Relation scoring is not part of this recovery command.

The recovery is reported separately from the registered attempt. Its terminal
status counts, format-repair count, budget exhaustion and subsequent analysis
must remain visible. A successful recovery cannot retroactively turn the
original attempt into a clean registered execution. Any analysis including
repaired outputs requires a sensitivity result that treats those outputs as
unaccepted. Raw responses, per-request results and private file paths remain
outside the public repository.

## Forward correction after the stopped pilot

The first recovery pilot stopped on its first A1 request (`semantic_failure`;
pilot-stop SHA-256 `2c64178f2d89c721c3c35ed5e5926613b5dc3f89cf7b6016133701770860671b`).
The provider response parsed but contained a citation, which A1's frozen arm
rule forbids. No full recovery batch ran. The stopped lease and response remain
private and immutable.

For a separate forward attempt (transport v3), only the outbound provider wire
schema is narrowed to require an empty `visible_evidence_ids` array for all three
citation-free arms (A1, B1, B2). The immutable gateway request and its original
local schema remain unchanged, as do prompts, evidence, model, sampling,
census and semantic checks. The four citation-required arms retain their exact
prior wire schema. No citation is removed from a received response. This is a
post-failure technical correction, not a retroactive pass for either failed
run; any later results must identify the new code version and be reported as
recovery evidence.
