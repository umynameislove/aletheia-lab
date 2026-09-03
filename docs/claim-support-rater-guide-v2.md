# Independent claim-support rating guide, version 2

This guide defines the human task used to validate the automatic claim-support
instrument. It applies to the version 2 synthetic onboarding exercise and the
later development-only validation sample. Onboarding is training and
qualification material and must never enter a scientific result.

## Evidence boundary

Judge only whether the visible evidence supports the exact written claim. Do
not use personal knowledge, browse for missing facts, infer hidden project
state, or reward a plausible diagnosis. Treat scope, population, magnitude,
duration, timing, causation, exclusivity, and recommended action as material
parts whenever changing them would change the meaning of the claim.

## Labels and precedence

Choose exactly one label by applying these rules in order:

1. `contradicted` — visible evidence directly conflicts with at least one
   material part of the claim. This label takes precedence even when another
   material part is supported.
2. `unsupported` — visible evidence neither establishes nor directly conflicts
   with any material proposition needed for the claim.
3. `partially_supported` — visible evidence establishes at least one material
   part, while another material part remains unestablished. No material part
   may be directly contradicted.
4. `fully_supported` — visible evidence establishes every material part.

The distinction between missing and conflicting evidence is decisive. Missing
evidence yields `unsupported` or `partially_supported`; evidence showing the
opposite of a material proposition yields `contradicted`.

## Required interpretation controls

- Temporal order alone does not establish causation.
- Correlation or co-occurrence alone does not establish causation.
- A sample or subgroup does not establish a population-wide claim.
- An aggregate does not establish the same effect in every subgroup.
- A measured value that differs from an asserted exact magnitude or duration
  directly contradicts that material assertion.
- Evidence of another cause contradicts claims using `sole`, `only`, or
  equivalent exclusivity language.
- Absence of a measurement is not evidence that the measured event did not
  occur.
- A source artifact being present does not establish that it was executed.

## Required procedure

For every claim:

1. read the complete claim and every visible evidence excerpt;
2. list the claim's material parts mentally;
3. check for direct conflict before checking support;
4. choose one label without consulting another rater or any model;
5. cite every excerpt used for the decision;
6. write a concise rationale identifying the established, missing, or
   conflicting material parts.

`unsupported` may cite no excerpt when no excerpt bears on the claim. Every
other label must cite at least one visible evidence ID. Never edit a claim,
evidence excerpt, packet identifier, workflow identifier, or decision order.
If an item is unreadable, duplicated, missing evidence, or technically
malformed, stop and return the packet to the coordinator rather than guessing.

## Completing the submission file

Keep `blind-packet.json` unchanged. Make a working copy of
`submission-template.json` named `completed-submission.json` and edit only the
nullable decision values and attestations in that copy.

For each decision:

- preserve `blind_claim_id` and the original list order;
- replace `support_label: null` with exactly one registered label;
- put only evidence IDs visible in that same claim in `evidence_ids_used`;
- use `[]` only for `unsupported` when no excerpt bears on the claim; and
- replace `rationale: null` with a specific explanation between 20 and 1,000
  characters.

Example structure using non-packet placeholder IDs:

```json
{
  "blind_claim_id": "blind-claim-<unchanged-value>",
  "support_label": "partially_supported",
  "evidence_ids_used": ["evidence-<visible-value>"],
  "rationale": "The excerpt establishes one material part, while the remaining part is unmeasured."
}
```

After all 20 decisions are complete, set the attestations to reflect the actual
process. A valid independent human submission states that it was completed by
the assigned human, completed independently, completed without model
assistance, and completed after reading this rubric. Do not assert a statement
that is not true; notify the coordinator instead.

Before returning the file, check all 20 decisions a second time for exact ID
order, citations, label spelling, non-null rationales, valid JSON syntax and
truthful attestations. Return only `completed-submission.json`; do not return an
edited packet or combine material from the other rater.

## Independence and confidentiality

Complete the packet alone. Do not use generative AI, model-as-judge tools,
another rater's decisions, the evaluator mapping, automatic labels, hidden
ground truth, or main or sealed outcomes. Rater names and contact information
stay outside repository artifacts; the packet uses only the assigned rater
slot.

Before submission, confirm that all decisions are complete, all cited evidence
IDs appear in the corresponding claim, rationales are specific, and all four
attestations are accurate. The coordinator validates and locks each completed
packet before opening any evaluator-only mapping or comparing raters.

## Qualification gate

The version 2 onboarding set contains 20 new synthetic cases balanced across
the four labels. A rater is ready for the main annotation task only when:

- macro-F1 against the coordinator-only answer key is at least 0.80; and
- no reference-contradicted claim is rated `partially_supported` or
  `fully_supported`.

A failed attempt requires rubric review and a new coordinator-authorized
training exercise. It does not change the frozen scientific thresholds.
