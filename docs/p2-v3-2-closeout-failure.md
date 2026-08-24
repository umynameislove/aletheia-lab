# V3.2 closeout failure and bounded recovery

## Terminal status

The registered v3.2 attempt terminated at `build_closeout` with a Pydantic
`ValidationError`. Both dataset execution calls had returned before that stage,
but the atomic failure policy intentionally published neither dataset attempt,
their metrics, nor a scientific disposition. The immutable terminal-store root
is `1ce2b827d027cdb0685ad22c520d1ff11b6fcb45e2af5894ad2f4f964c97d029`.
The original attempt must not be rerun, overwritten, or described as a positive,
negative, or abstained scientific result.

## Outcome-blind diagnosis

The diagnosis used only the terminal failure receipt, the reachable closeout
code path, and synthetic conformance evidence. It did not inspect or reconstruct
sealed outcomes. The inference contract defines two legitimate abstention paths:

1. calibration abstention occurs before dataset scoring and therefore contains
   no outcome, assumption family, inference, or decision;
2. scientific abstention occurs after complete dataset scoring when a registered
   assumption fails and therefore must retain both inferences, the three
   assumption families, and the fail-closed decision.

The v3.2 closeout validator accepted the first representation but rejected the
second solely because its disposition was `abstain`. A synthetic complete run
with a failing registered MMD family reproduces the same exception class at the
same closeout stage. This is an implementation-contract contradiction, not a
change to the estimand, model, threshold, inference, or decision rule.

Because the persisted exception contains only a message digest and the dataset
attempts were deliberately not published, the audit labels causal attribution
as high confidence rather than claiming an unavailable exception-message
preimage. No scientific result can be recovered from the v3.2 terminal store.

## Permitted correction

The corrected state contract accepts exactly two complete shapes:

- calibration abstention: all analysis fields absent and no assumption family;
- scientific closeout, including scientific abstention: both dataset inferences,
  all three assumption families, and the bound study decision are present.

Mixed or partial shapes continue to fail closed. The correction cannot authorize
a v3.2 replay. Any prospective execution requires a new protocol identity,
annotated tag, immutable release, registration receipt, and one-use execution
marker. The v3.2 terminal failure remains part of the publication record.
