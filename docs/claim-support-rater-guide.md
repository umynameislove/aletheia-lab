# Independent claim-support rating guide

This guide defines the human task used to validate the automatic claim-support
instrument. It applies to both the synthetic onboarding exercise and the later
development-only validation sample. The onboarding exercise is training and
qualification material; it must never enter a scientific result.

## What to judge

Judge only whether the **visible evidence** supports the exact written claim.
Do not use personal knowledge, browse for missing facts, infer hidden project
state, or reward a plausible diagnosis. Break the claim into its material parts:
changing any part that would alter the diagnosis, scope, magnitude, timing, or
recommended action makes that part material.

Choose exactly one ordered label:

1. `contradicted` — visible evidence conflicts with at least one material part;
2. `unsupported` — visible evidence neither establishes nor materially conflicts
   with the claim;
3. `partially_supported` — visible evidence establishes some, but not all,
   material parts;
4. `fully_supported` — visible evidence establishes every material part.

Plausibility is not support. Temporal order alone is not causation. Evidence for
an aggregate does not automatically establish every subgroup, and evidence from
one subgroup does not establish a universal claim. When both supporting and
conflicting evidence exist, use `contradicted` if the conflict concerns a
material part; otherwise use `partially_supported`.

## Required procedure

For every claim:

1. read the entire claim and all visible evidence excerpts;
2. identify its material parts;
3. choose one label without consulting another rater or any model;
4. cite every excerpt used for the decision;
5. write a concise rationale that states which material parts are or are not
   established.

`unsupported` may cite no excerpt when none bears on the claim. Every other
label must cite at least one visible evidence ID. Never edit a claim, evidence
excerpt, packet identifier, or workflow identifier. If an item is unreadable,
duplicated, missing evidence, or technically malformed, stop and return the
packet to the coordinator; do not guess and do not submit an incomplete packet.

## Independence and confidentiality

Complete the packet alone. Do not use generative AI, model-as-judge tools,
another rater's decisions, the evaluator mapping, automatic labels, hidden
ground truth, or main/sealed outcomes. Rater names and contact information stay
outside repository artifacts; the packet uses only the assigned rater slot.

Before submission, confirm that all decisions are complete, all cited evidence
IDs appear in the corresponding claim, rationales are specific, and the four
attestations are accurate. The coordinator locks each completed packet before
opening any evaluator-only mapping or comparing raters.

## Onboarding gate

The onboarding set contains 20 synthetic cases balanced across the four labels.
A rater is ready for the main annotation task only when macro-F1 against the
synthetic answer key is at least 0.80 and no reference-contradicted claim is
rated `partially_supported` or `fully_supported`. A failed onboarding attempt
requires rubric review and a new coordinator-authorized training exercise; it
does not change the frozen scientific protocol or its thresholds.

