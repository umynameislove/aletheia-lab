# Evidence-bounded diagnostic policy development

This pilot compares an A3-derived citation/abstention instruction with an A4
rival-exclusion instruction on the existing evaluator-mapping development corpus.
It does not repeat the historical diagnosis study or tune on new-source final
results. The shared question, candidate census, evidence, output grammar, model,
token limits, parser and tool access are identical. Only the policy instruction
changes. A3 can abstain and request evidence too.

## Question and reference

The pilot asks whether explicit rival exclusion reduces unwarranted unique
attribution **without losing resolution when a decisive witness is present**.
It retains all twelve old constructed pairs: two sources, two estimators and
three nonzero doses, with two fault worlds and four evidence views per pair.
These are 96 views, not 96 independent families. The two source clusters remain
the principal limitation to generalization.

Old model artifacts are hash-checked before loading. Only historical
train/development predictions are recomputed, and saved swap ledgers are replayed
without searching for better pairs. Mapping correction and target-binding
correction are checked independently. Every projected view must match its old
summary. No new-source final prediction or outcome is read.

The reference is computed from **visible witness semantics**, before generation.
Wrong decoder order, coherent example decoding, intact source-target binding and
a matching independent recomputation resolve the mapping world. Intact decoder
order, changed source-target binding and unchanged decoder recomputation resolve
the target-binding world. A scalar loss or feature-distribution clue alone does
not distinguish them. The reference assumes trusted source checks and exactly
one of these two constructed faults; it is not a posterior, conformal set,
population guarantee or mechanism admission.

`missing_key` retains both compatible candidates. `full`, `noisy` and
`misleading` retain the decisive checks. The cohort feature distance in
`misleading` is a distractor, **not contradictory evidence**. This first pilot
does not claim to measure conflict routing. A multi-fault signature is outside
the declared single-fault scope, not automatically a factual contradiction.

## Controlled output and baselines

The common schema supports singleton, candidate set, abstention and a proposed
column/target provenance measurement. It has no free-text rationale or remediation.
Additional definitive cause assertions have a separate `causal_claims` field and
are scored even when the action says abstain. Citations name visible observation
fields; citation validity alone does not establish a cause.
Evidence-unsupported commitment and incomplete witness citation are separate
outcomes. Both policies share the same requirement to cite all three witness
fields for a compliant resolved singleton. Missing a citation on sufficient
evidence is a citation error, not reclassified as an evidence-unsupported cause.

This restricted grammar makes action warrant mechanically assessable. It does
**not** validate free-prose atomic claim faithfulness or prove that a natural
language diagnosis follows the same policy. A singleton candidate set that
excludes a still-compatible rival is counted as unsupported exclusion. A full
two-candidate set is bounded but uninformative, not a resolved diagnosis.
A one-candidate set is a unique attribution regardless of its action tag: it
gets the same warrant/resolution scoring as a singleton. Unsupported commitment
and exclusion counts can overlap; they must not be added as disjoint events.

Two zero-cost controls receive the same visible facts: always-abstain and a
deterministic visible-witness rule. They are engineering/reference controls,
not measured LLM behavior. Always-abstain has zero full resolution, and its
selective warranted-commitment rate is undefined (`null`), not perfect accuracy.

## Execution and analysis

Byte-identical contexts share one completion **within each policy**, including
the two indistinguishable missing-key worlds. Thus there are 84 distinct contexts
and at most 168 paid calls. Their retained decisions are joined back to all 96
views. Deduplication is explicit, not extra independent evidence or repeated
sampling. Policy order alternates within context blocks; no hidden cause, source,
dose, condition name or reference is sent to the provider.

The fixed snapshot is `gpt-4.1-2025-04-14`: temperature 0, seed 731, maximum
8,192 reserved input tokens and 1,024 output tokens, 90-second timeout, one call
per unique policy/context, no SDK retries, no tools and `store=false`.
Preparation checks the actual strict transport schema and token reservation.
Python and the SDK/parser/tokenizer package versions are bound in the plan along
with the directly used runtime code. Execute and verify with that same environment.
The full-input/output reservation is **$4.128768** at the recorded $2/$8 per
million input/output token rates; the operator ceiling is $4.25, not an expected
bill. Actual observed usage and unknown-usage reservations are reported separately.
An unknown charged attempt reserves the full per-call ceiling. Three consecutive
invalid/provider responses stop the pilot; failures and unexecuted views remain
visible, not converted to safe abstention. Private responses stay outside Git.
`store=false` does not promise zero provider retention.

Analysis reports ambiguous unsupported commitment, unsupported candidate
exclusion, bounded non-answer behavior, wrong non-answer reasons, full resolution,
noisy/misleading resolution retention, hidden-cause errors, technical failures,
tokens, latency and cost separately. Paired differences average the two worlds
within each constructed pair. Scheduled, executed, unexecuted and failed views
are distinct from unique requests and charged provider attempts. Failures and
unexecuted views contribute no all-planned resolution or bounded-success credit.
An assessable-only safety rate with no assessed outputs
is `null`. No population confidence interval, superiority margin or efficacy
claim is derived from this small development corpus.

## Commands

Use the project Python environment with `PYTHONPATH=src`. Preparation is offline:
It requires retained feasibility and symptom-matching artifacts from the
[development guide](evaluator-score-mapping-development.md), not a fresh final run.

```sh
python scripts/evidence_bounded_policy_pilot.py prepare --root . --memory-root ../memory
```

If the already-pinned old archives live in another checkout, use `--source-root`
to read them there without copying. The current root still supplies the protocol;
source bytes must match the saved artifact hashes. The default output is one
private `evidence-bounded-policy-development-v1` directory. Preparation does not
contact a provider. Check its payload/destination and fresh plan digest before
authorizing paid execution:

```sh
python scripts/evidence_bounded_policy_pilot.py execute --memory-root ../memory --confirm-plan-sha256 DIGEST
python scripts/evidence_bounded_policy_pilot.py verify --memory-root ../memory
```

Do not execute again over an existing lease. Verification reparses persisted
responses and rebuilds analysis and the receipt without network access. Offline
fixtures prove contracts, not A4 efficacy; live results are required to assess
headroom. No protected contrastive study is authorized by this pilot.

## Methodological basis

Reject-option theory motivates reporting risk and coverage together, rather
than rewarding zero-answer policies ([Franc, Prusa and Voracek, JMLR 2023](https://jmlr.org/papers/v24/21-0048.html)).
Set-valued classification separates truth inclusion from set size, but its
statistical coverage is not the witness compatibility used here
([Sadinle, Lei and Wasserman](https://arxiv.org/abs/1609.00451)).
Context sufficiency motivates separating missing-context errors from failure to
use sufficient context ([Joren et al., ICLR 2025](https://arxiv.org/abs/2411.06037)).
The recent [EviScope preprint](https://arxiv.org/html/2609.17081v1) tests paired
evidence interventions and reports that explicit gates can underperform simpler
prompts. It supports testing the safety/resolution tradeoff, not presuming A4 wins.

The intended research contribution is an auditable link from source-bound fault
and correction to a visible rival-exclusion witness and then a warranted action.
Abstention, candidate lists and evidence routing themselves are not claimed new.
