# V3.2 late-reference-freeze amendment

## Status and scope

This amendment records a process-sequencing deviation in the real 20-claim
V3.2 onboarding exercise. It was written after the onboarding submissions and
qualification metrics were known, but before either held 200-claim main packet
was released. It is therefore a retrospective disclosure for onboarding and a
prospective release condition for the main human study. It is not a
preregistration of the events that already occurred.

The scientific disposition is **PASS WITH GAP**. Both primary rater slots met
the frozen onboarding metric gates against the subsequently locked independent
human reference, but that reference was not locked before the primary
onboarding packets were delivered and completed. The later blinding and lock
controls reduce the resulting bias risk; they cannot restore the missed
prospective order or support an unqualified `PASS`.

This amendment neither releases the main packets nor changes a scientific
outcome. Main-packet release remains fail-closed and requires a separate
release receipt after this amendment is approved.

## Intended and observed order

The intended order was:

1. construct the real onboarding packet and independently author and lock its
   human reference;
2. deliver the two blind onboarding packets;
3. validate and lock both independent primary submissions;
4. assess the two submissions against the locked reference using the frozen
   gates; and
5. consider release of the already prepared main packets through a separate
   authorization boundary.

The observed order was:

1. the disjoint 20-claim onboarding set and held 200-claim main set were
   prepared; the historical preparation manifest recorded the reference as
   pending and main release as unauthorized;
2. a five-run, same-family AI-panel consensus was locked at
   `2026-09-13T16:47:11.872950+00:00`, before the audit first read either
   existing primary submission; this was an advisory audit boundary, not a
   qualification or reference-key step;
3. both completed primary onboarding submissions existed and were
   structurally prechecked on 2026-09-13, before an independent human reference
   was authored and locked;
4. a new reference-author packet was prepared from the same ordered 20 claims
   without either primary submission, automatic or sealed labels, AI-panel
   material, an expected label distribution, or a disclosed disagreement;
5. all 20 reference decisions were byte-locked at
   `2026-09-14T06:44:37.828505+00:00`, before the official qualification
   comparison;
6. a candidate-bound attestation addendum recorded that the same human rated
   all 20 claims before discussion and had not viewed either primary
   submission, the AI panel, or automatic or sealed labels;
7. the independent reference was finalized at
   `2026-09-14T06:51:50.127506+00:00`, and the qualification assessment was
   created only after that lock; and
8. the assessment kept `main_packet_release_authorized=false` and named this
   amendment as the remaining release blocker.

The historical preparation manifest is preserved as written. Its pending and
delivery flags describe the state recorded at preparation time; this amendment
does not rewrite them to simulate the intended chronology.

## Content-addressed evidence

The following public contract identities remain authoritative:

| Artifact | Identity kind | SHA-256 |
| --- | --- | --- |
| V3.2 closeout merge commit | Git commit | `e44ffc181562915efbd1d0d710f5239008b23eba` |
| validation protocol | canonical payload | `71c3f36c71e6d6294e6dba9b4e12db6c53148f718a17bf7f347e77cc49bae77d` |
| human workflow v2 | canonical payload | `f65434be6ae464578958f571a560ffb91025f03503c51d06047f11c949795401` |
| V3.2 reading supplement | file bytes | `a45ba6243459621d08f8de6e0e38a4c824c2e76a7d011b0b4fec9ef7a77436ff` |
| private preparation manifest | execution-canonical payload | `aa2c42e8c9742b939d311a7a4417b1fae3ae90c1421010a8b3bd2639b3d0cfd6` |
| prepared-study receipt | canonical payload | `3c62cf70df33904d51cb5ada5e1f92a5e30ddbb9289240176c5eac5685bbacf8` |
| evaluator mapping | canonical payload | `7b9e4d79cd232326bbd773e376210fbbf5a7c432caf474fc46867ec33e5a4786` |

The private evidence remains outside the repository. Only its content
identities are disclosed here:

| Private artifact | Identity kind | SHA-256 |
| --- | --- | --- |
| independent reference source packet | canonical payload | `f2f7d76e198e1dedc859ec1690e3e3ab674cfada55f416622a58cfbccc12a719` |
| independent reference candidate | file bytes | `0c64f1514bea9955bea3fc905f404236cc943982da04443830530ef3895ea59e` |
| independent reference decisions | canonical payload | `dd06242d9d69f66fd9866501b7ebf0cb95b328095fa54d46ff7d4478ecc2c629` |
| reference-author attestation addendum | file bytes | `b16aafc21e59ed25670cf8c977c67d77ff681446a18b5f4b146220dabd14544c` |
| finalized independent reference lock | canonical payload | `4aca3996c408bbfbf5cce5ad69c95b1a72ca3fabe5977fe4a5b753da0e4278ce` |
| primary rater-slot 1 submission | file bytes recorded at precheck | `beea3bf276bde77dea216dad864bfd687dba254cbc3d5daf329a46ee2cb175b4` |
| primary rater-slot 1 completed packet | canonical payload | `94c1716614112bd2bb5d97d89679d0224ba9db53c65143048703840eaaa173bc` |
| primary rater-slot 2 submission | file bytes recorded at precheck | `7e50bbaf5082b894f9152d02169332b5ee4d4f7e2a4b75485083dbbeccb3d215` |
| primary rater-slot 2 completed packet | canonical payload | `95aebaac547c6220f4fa85cb94aa3cb03176ec9ec9ab5b2c952d95eabc32c431` |
| qualification assessment | canonical payload | `9ebc01fb149e233be82a8e94e28ebb75f6a8c84306415ff055eaa6c8db4c0de6` |
| advisory AI-panel consensus | file bytes | `757a3bd0807c1e126f4264046cff76759f6918e8a420392ca6e2a9ca5179e4b2` |

The raw submissions, reference decisions, rationales, item-level labels,
disagreement identity, personnel information and local paths remain private.
The submission byte identities above were recorded by the prechecks, verified
unchanged in the AI-panel audit's final integrity check and cross-bound by the
final assessment. Publishing their source files is neither necessary nor
permitted.

## Frozen qualification result

The qualification used the unchanged onboarding gates:

- macro-F1 at least `0.80`; and
- zero cases where a reference-contradicted claim was rated as partially or
  fully supported.

Rater-slot 1 achieved macro-F1 `1.0` with zero critical false-support cases.
Rater-slot 2 achieved macro-F1 `0.9494949494949495` with zero critical
false-support cases. Both statuses are `ready_for_main_annotation`. The two
primary raters agreed on 19 of 20 onboarding claims. These are qualification
observations only; the onboarding set remains excluded from all scientific
denominators and does not establish main-study reliability.

## Mitigations and unchanged decisions

The late-reference risk is mitigated, but not erased, by all of the following:

- the reference author rated the full 20-claim set rather than only the known
  disagreement;
- the reference packet excluded both primary submissions, evaluator-only
  material, automatic or sealed labels, AI-panel material and disagreement
  hints;
- the reference decisions were locked before the missing blinding attestations
  were completed and before the official comparison;
- the addendum was bound to both the candidate bytes and the canonical decision
  payload, preventing an attestation response from changing a decision;
- structural validation checked the claim census and order, packet identity,
  citations, rationales and declared blinding before the reference was
  finalized; and
- the qualification assessment did not open automatic labels, sealed labels or
  main outcomes.

No outcome-dependent design change is authorized. In particular, this
amendment leaves unchanged:

- the four-label rubric, conflict precedence and V3.2 evidence-reading rules;
- the two qualification thresholds above;
- the disjoint 20-claim onboarding set and its exclusion from scientific
  analysis;
- the already materialized 200-claim main sample, its 50-per-automatic-label
  allocation and all sampling caps;
- the two distinct main packet identities and evaluator mapping;
- the rule that both main submissions must be independently validated and
  locked before the evaluator mapping is opened;
- adjudication of every rater disagreement and every claim that either rater
  marks contradicted; and
- the registered metrics, family-clustered uncertainty procedure, exclusions
  and analysis plan.

The AI panel has no qualification, reference-key, gold-label or adjudication
role. Its five graders are correlated runs from one model family, not five
independent human raters. Its locked output may be reported only as advisory
robustness or sensitivity evidence after human labels are locked; it cannot be
pooled with human votes or used to alter this disposition.

## Limitations and release gate

Blinding and human identity are supported by a coordinator-relayed attestation,
not independently observed behavior. Software verifies content binding and the
materials placed in the reference packet, but cannot prove that no off-system
exposure occurred. The missed order therefore remains a real procedural gap.

Merging this amendment records acceptance of that bounded gap. It does not
authorize external delivery. A separate fail-closed release receipt must still
verify the qualification-assessment identity, both rater readiness statuses,
the unchanged held ZIP identities and contents, the rater-slot mapping, absence
of labels or reference material from rater surfaces, and the continued closure
of main outcomes. Any mismatch keeps both main packets held.
