# Claim-support corpus and human-validation delivery roadmap

This roadmap is the authoritative delivery sequence from the outcome-blind
corpus protocol to independent human validation. It prevents implementation,
corpus generation and human annotation from being conflated and records the
exact gate that permits each transition.

The scientific contracts remain authoritative over schedule. Finishing code
early does not authorize a provider call, claim materialization or human-study
interpretation before its gate passes.

## Current state

| Work package | Branch | Current state | Outcome boundary |
|---|---|---|---|
| PR 1 — CSV-01 to CSV-07 | `research/claim-support-corpus-protocol` | Implemented locally; PR, CI and merge pending | Zero provider calls, claims, labels and human judgments |
| PR 2 — CSV-08 to CSV-15 | `feat/claim-support-corpus-materializer` | Locked behind PR 1 merge | Implementation and readiness only; no real claims |
| PR 3 — HVAL-01 to HVAL-14 | `feat/claim-support-human-workflow` | Locked behind PR 2 interface freeze | Fixture-only workflow; no fabricated human result |
| PR 4 — CSV-16 to CSV-33 | `research/claim-support-corpus-freeze` | Locked behind PR 2 and PR 3 | Development-only outputs and automatic labels; no main/sealed outcome |
| Human run — AH-02A/B and AH-03 | Private packets; no public annotation branch | Locked behind immutable PR 4 closeout | Real independent judgments and adjudication |

The current PR 1 receipt is
`corpus_protocol_frozen_source_expansion_required`. The declared inventory has
five data-drift families and can supply at most 25 claims per automatic label
under the five-claims-per-family cap. The target remains 50 per label and 200 in
total. This is a correct readiness finding, not permission to reduce the sample.

## Non-negotiable study invariants

1. The validation sample remains exactly 200 real development claims: 50
   `contradicted`, 50 `unsupported`, 50 `partially_supported` and 50
   `fully_supported`.
2. The separate onboarding set contains exactly 20 real development claims and
   is disjoint from the 200-claim validation sample.
3. Synthetic claims, duplicated claims, padding and main/sealed outcomes are
   forbidden in both sets.
4. One family contributes at most five claims to one automatic-label stratum;
   one output contributes at most two.
5. Every label stratum contains at least ten distinct families and at least 25
   distinct outputs.
6. B3 remains external-native and non-pooled. Only A1–A3, B0–B2, CodeGraph and
   FULL may enter the normalized claim corpus.
7. Automatic labels are frozen before claim materialization and cannot be
   rewritten by human judgments.
8. Reserve families can replace only a pre-execution technically ineligible
   family in registered mechanism-local order. Observed outputs, labels or human
   judgments cannot trigger reserve use.
9. A completed census with an insufficient label stratum terminates the current
   study as `insufficient_label_stratum`; it does not authorize a smaller sample,
   adaptive expansion or threshold changes.
10. Human packet contents and individual judgments are not committed to a
    public branch. Only sanitized, adjudicated aggregate artifacts may be
    published after privacy and completeness review.

## PR 1 — Prospective corpus protocol

**Branch:** `research/claim-support-corpus-protocol`  
**Scope:** CSV-01 to CSV-07  
**Exit state:** protocol valid, current insufficiency preserved, no outcomes

| ID | Deliverable | Acceptance gate |
|---|---|---|
| CSV-01 | Bind parent validation protocol, fairness freeze, mechanism filter and family inventory by exact path and SHA-256 | Byte change, symlink or repository escape fails before interpretation |
| CSV-02 | Freeze development-only evidence-accountability eligibility for all three registered mechanism tracks | Mechanism dispositions are preserved; non-admitted causal scoring remains forbidden |
| CSV-03 | Freeze 15 primary families, six ordered reserves and three evidence conditions | Family/output caps mathematically reconcile with 50 claims per label |
| CSV-04 | Freeze eight normalized variants and exclude B3 | No external-native output is converted or pooled post hoc |
| CSV-05 | Freeze the schema-native atomic-claim boundary | No sentence splitting, punctuation splitting or free-text fallback; at most five claims per output |
| CSV-06 | Freeze four automatic support definitions, precedence and visible-input boundary | Hidden truth, mechanism, condition, variant, human label and main outcome are unavailable to the instrument |
| CSV-07 | Freeze contingency rules, feasibility receipt, verifier, documentation and CI coverage | Receipt is hash-derived, reproducible and fail-closed; materialization gate returns blocking status |

PR 1 is complete only after the branch is pushed, all required CI checks are
green, review confirms the outcome-blind boundary and the PR is merged. Its
insufficient-census receipt must remain auditable after later amendments.

## PR 2 — Materialization readiness and storage

**Branch:** `feat/claim-support-corpus-materializer`  
**Scope:** CSV-08 to CSV-15  
**Dependencies:** merged PR 1  
**Exit state:** `materialization_ready=true` with every outcome flag false

| ID | Deliverable | Required behavior |
|---|---|---|
| CSV-08 | Prospective family-inventory amendment | Register five primary and two reserve family identities per mechanism, seeds, sources, invariants and three evidence conditions before provider calls; preserve the PR 1 receipt rather than rewriting it |
| CSV-09 | `diagnosis-output/2` schema manifest | Self-hashing schema with schema-native atomic claims, claim type, visible evidence IDs, source-record binding, abstention and parse-failure representation |
| CSV-10 | Eight normalized variant adapters | A1–A3, B0–B2, CodeGraph and FULL emit the same output contract without changing their frozen information/tool budgets |
| CSV-11 | Automatic support-relation instrument and implementation manifest | Implement the four frozen decision rules using only claim text/type and visible evidence; bind source, tests and protocol hash before any real extraction |
| CSV-12 | Deterministic corpus materializer | Normalize eligible development outputs into content-addressed claim-pool entries; reject main/sealed, B3, duplicate and non-atomic sources |
| CSV-13 | Immutable corpus store | Create-only objects and manifests, atomic publication, idempotent replay, cross-platform paths and no overwrite semantics |
| CSV-14 | Independent corpus verifier | Reader/verifier does not trust writer state; detects tamper, missing object, partial publication, duplicate identity, source mismatch and leakage |
| CSV-15 | Readiness closeout | Bind the complete 15-family primary request census and six-family reserve census; prove all six PR 1 blocker classes are cleared without provider calls |

The primary census contains `15 families × 3 conditions × 8 variants = 360`
registered development requests. The reserve census contains
`6 × 3 × 8 = 144` predeclared requests, but reserve requests execute only after
a recorded pre-execution technical-ineligibility decision. PR 2 does not execute
either census.

PR 2 exit assertions:

```text
materialization_ready = true
provider_calls_executed = false
development_claim_pool_materialized = false
automatic_labels_generated = false
human_annotations_collected = false
main_or_sealed_outcomes_opened = false
```

## PR 3 — Human-workflow infrastructure

**Branch:** `feat/claim-support-human-workflow`  
**Scope:** HVAL-01 to HVAL-14  
**Dependencies:** PR 2 schemas/interfaces frozen  
**Exit state:** complete fixture-tested workflow, no real judgment

| ID | Deliverable | Acceptance gate |
|---|---|---|
| HVAL-01 | Role and access model | Rater 1, rater 2, adjudicator and study custodian permissions are disjoint |
| HVAL-02 | Onboarding packet schema | Exactly 20 disjoint claims, visible evidence and blank decisions; no validation-sample IDs |
| HVAL-03 | Versioned annotation handbook/rubric binding | Packet binds the exact handbook and label-order hash used for training and scoring |
| HVAL-04 | Blind validation packet schema | Exactly 200 opaque claim IDs with only claim text and visible evidence |
| HVAL-05 | Independent assignment generator | Rater-specific packet identities are different while the underlying blinded claim census is identical |
| HVAL-06 | Evaluator mapping isolation | Automatic labels, source IDs, family, condition, variant and mechanism remain evaluator-only |
| HVAL-07 | Completed-packet schema | Requires one valid decision per assigned blind ID, evidence IDs used and bounded rationale fields |
| HVAL-08 | Completeness and integrity validator | Rejects missing, extra, duplicate, malformed, cross-packet or post-lock decisions |
| HVAL-09 | Independent submission lock | One rater cannot inspect or alter the other rater's submission; replays are idempotent and conflicting writes fail |
| HVAL-10 | Adjudication queue builder | Includes every disagreement and every item where either rater selects `contradicted` |
| HVAL-11 | Adjudication terminal contract | Preserves both original judgments, final judgment, reason and rubric-version decision |
| HVAL-12 | Frozen scoring pipeline | Computes raw agreement, quadratic-weighted kappa, macro-F1, false-supported and contradicted-to-supported rates plus clustered bootstrap |
| HVAL-13 | Privacy-safe export | Strips rater identity and private packet paths while preserving aggregate auditability |
| HVAL-14 | Fixture-only lifecycle closeout | Tamper, replay, incomplete packet, label leakage and false-pass tests succeed; receipt states no real human validation occurred |

Fixture or synthetic records may test the workflow machinery but cannot be
reported as instrument validation evidence.

## PR 4 — Development census and immutable corpus freeze

**Branch:** `research/claim-support-corpus-freeze`  
**Scope:** CSV-16 to CSV-33  
**Dependencies:** PR 2 readiness closeout and PR 3 lifecycle closeout  
**Exit state:** immutable 20-claim onboarding set and 200-claim blind sample

| ID | Execution step | Gate or terminal behavior |
|---|---|---|
| CSV-16 | Verify PR 2 and PR 3 terminal receipts from a clean synchronized main checkout | Any hash, environment, schema or outcome-boundary mismatch blocks execution |
| CSV-17 | Issue development-only execution authorization | Authorization binds exact commit, census, model, budgets, schemas and one-attempt semantics |
| CSV-18 | Execute the complete 360-request primary census | No output-driven early stop; raw response is persisted before parse |
| CSV-19 | Apply registered reserve replacement, if needed | Only pre-execution technical ineligibility may activate the next mechanism-local reserve; activation is immutable |
| CSV-20 | Reconcile request/output census | Every authorized request has one terminal success or technical-failure record; no silent drops |
| CSV-21 | Normalize eight variant outputs | Adapter and schema hashes match PR 2; parse failures remain denominator-visible |
| CSV-22 | Extract schema-native atomic claims | At most five per output; no free-text recovery; source record and visible evidence resolve |
| CSV-23 | Apply the frozen automatic relation instrument | Instrument code/manifest hash matches PR 2; no human or hidden field is read |
| CSV-24 | Publish the immutable full claim pool | Entry, object, manifest and pool identities are content-derived and reproducible |
| CSV-25 | Audit duplicate and concentration risk | No duplicate identity/text-evidence record; family/output contribution caps are enforceable |
| CSV-26 | Audit leakage and visibility | No hidden truth, evaluator metadata, condition/variant/mechanism hint or main/sealed record reaches blind fields |
| CSV-27 | Audit provenance and replay | Every claim traces to request, raw output, normalized output and visible evidence; independent replay reproduces identities |
| CSV-28 | Evaluate prespecified stratum feasibility | Each label has at least 50 eligible claims, ten families and 25 outputs; otherwise terminal `insufficient_label_stratum` |
| CSV-29 | Select and freeze the 20-claim onboarding set | Real, balanced, representative and disjoint from the validation set; no validation outcome inference |
| CSV-30 | Select exactly 200 validation claims | Exactly 50 per label using the frozen deterministic sampler and caps; no padding or duplication |
| CSV-31 | Generate two blind packets and isolated evaluator mapping | Same 200 underlying claims; distinct packet IDs; no rater-visible automatic/source metadata |
| CSV-32 | Run independent freeze audit | Clean process/hash-seed replay reproduces corpus, sample, packet and mapping identities |
| CSV-33 | Publish terminal development-corpus closeout | Records counts, exclusions, failures, hashes and zero human judgments; authorizes onboarding only |

PR 4 does not open main or sealed evaluation outcomes. If CSV-28 fails, the
current study closes without a human run. Any future corpus study must be a new
prospective registration and cannot be presented as an adaptive repair of this
sample.

## Human run — AH-02A, AH-02B and AH-03

Human work starts only after CSV-33 passes. The custodian distributes private
packets directly; individual packet and response files never enter a public PR.

### AH-02A — Onboarding and qualification

1. Kiên and Quân receive separate copies of the frozen 20-claim onboarding
   packet and the same versioned handbook.
2. Each works independently without AI summarization or access to the evaluator
   mapping, validation corpus or the other rater's decisions.
3. The study lead checks completeness, evidence-ID use, label-rule compliance
   and critical confusion patterns. Feedback is permitted only on onboarding
   items.
4. The qualification threshold and retry policy must be frozen in the human-run
   authorization before packets are opened. They may not be invented after
   seeing either rater's performance.
5. Failure to qualify blocks that rater from AH-02B; it does not alter the 200
   validation claims.

### AH-02B — Independent blind annotation

- Both qualified raters independently label all 200 claims.
- Neither sees automatic labels, source identities, family, condition, variant,
  mechanism, hidden truth or the other submission.
- Packet completeness is 100%; missing or extra decisions block scoring.
- Submissions are locked before the evaluator mapping or disagreement list is
  opened.

### AH-03 — Adjudication and instrument decision

The adjudicator resolves every disagreement and every item marked
`contradicted` by either rater. The final report preserves both original labels
and computes the prespecified gates:

| Endpoint | Acceptance threshold |
|---|---:|
| Quadratic-weighted Cohen's kappa | at least 0.70 |
| Automatic versus adjudicated macro-F1 | at least 0.80 |
| False-supported rate | at most 10% |
| Contradicted-to-supported rate | at most 5% |

All four gates must pass. The report also publishes raw agreement, prevalence,
the complete four-by-four confusion matrix and 2,000 family-clustered bootstrap
replicates. Failure of any gate blocks confirmatory automatic support-rate claims
and cannot be repaired by threshold, label, sample or denominator changes.

## Transition matrix

| From | Required evidence | Next authorized action | Forbidden shortcut |
|---|---|---|---|
| PR 1 | Green CI, merged protocol and preserved insufficiency receipt | Build PR 2 readiness infrastructure | Edit PR 1 receipt to claim enough families |
| PR 2 | `materialization_ready=true`, all outcome flags false | Build/merge PR 3 and prepare PR 4 authorization | Execute provider or extract real claims |
| PR 3 | Fixture-only lifecycle closeout | Authorize PR 4 development census | Treat fixture judgments as human evidence |
| PR 4 | CSV-33 immutable closeout with 20+200 disjoint sets | Run AH-02A onboarding | Send fewer than 200 validation claims |
| AH-02A | Both named raters meet prospectively frozen qualification | Open private AH-02B packets | Train or retry on validation claims |
| AH-02B | Two complete locked independent submissions | Open adjudication mapping and run AH-03 | Discuss labels before both submissions lock |
| AH-03 | Complete adjudication and all four gates evaluated | Freeze preregistration/main-run manifest if gates pass | Tune rubric, thresholds or sample after results |

## Synchronization ownership

- This roadmap owns PR/task order and transition gates.
- `claim-support-corpus-protocol.md` owns prospective corpus science.
- `claim-support-instrument-validation.md` owns the human-validation endpoints.
- Machine-readable protocol and receipt JSON own exact frozen values and hashes.
- The external Plan V5.2 task ledger owns delivery status.
- The executive workbook is a derived view and must be regenerated or patched
  whenever this roadmap, task ledger or transition status changes.
