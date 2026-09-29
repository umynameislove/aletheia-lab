# Paired human-label impact pilot for P5

The frozen P5 recovery has 32 equally weighted case families. The earlier
200-claim domain audit measured claim-level agreement, but only 44 of the 636
claims affecting the B1–A3 core contrast have human-final labels, and no
scored output is completely labeled. Its claim-level agreement cannot be
substituted for output- or family-level loss. This follow-on is a **secondary,
post-outcome measurement-sensitivity study**, not a rerun or amendment of the
registered P5 analysis.

## Sampling and estimand

The offline preparer takes an equal-probability sample without replacement of
eight entire families from the frozen 32-family frame. HMAC ranking uses a
fresh private seed and family identity only: not arm, condition, loss, claim
count, or automatic/human label. Each family has inclusion probability 8/32.
The two raters independently see **all claims in every scored B1 and A3
output** for the selected family in `full`, `missing_key`, and `noisy`. The
technical-failure and abstention slots stay in the family denominator with
their frozen losses; no claims are invented for them. The private coordinator
retains the family/arm/condition/automatic-label mapping. Neither rater ZIP
contains that mapping or a machine label.

For family `f`, let `D_f` be the mean over the three conditions of the
human-minus-automatic loss change for B1, less the same change for A3. Each
scored output loss is the **mean harm over all its claims**, with the frozen
primary weights 0 for fully/partially supported and 1 for unsupported or
contradicted. The prespecified sensitivity gives partial support weight 0.5.
The pilot estimate of the finite-frame human-relabel contrast is the frozen
32-family automatic contrast **plus the mean of sampled `D_f`**. Its design
variance is `(1 − 8/32) × s_D² / 8`, using eight *paired families* as the
sampling units. Claims and output pairs are not independent replicates.

Eight families are a variance/feasibility pilot, not a claim of 95% precision.
The script reports a normal-approximation half-width **for planning only** and
an FPC plug-in total-family count for a predeclared 0.05 half-width, anchored
to P5's frozen 0.05 minimum-effect threshold. If pilot variance is zero, it
reports no sample-size answer: zero observed variation
is not proof of zero population uncertainty. A larger sample must be selected
from remaining families under a separately fixed continuation design before
new labels are seen; do not reuse the pilot estimate as a fixed-size final
confidence interval after outcome-dependent stopping. A complete 32-family
census removes family sampling error for this finite benchmark but not rater
error or limits to generalization.

The sampling-unit/finite-population correction follows [Statistics Canada's
survey sampling guidance](https://www150.statcan.gc.ca/n1/pub/12-001-x/2019002/article/00006/02-eng.htm).
Pilot precision estimates are inherently unstable at small sample sizes, as
discussed in the [CONSORT pilot extension](https://www.bmj.com/content/355/bmj.i5239).
These sources support the sampling logic, not a claim that the corrected P5
effect is already known.

## Human and privacy boundary

The old rubric is retained. Two human submissions must cover the exact same
blind items and be locked before looking at machine labels or comparing
answers. A third human receives only disputed claim/evidence pairs and the
two independent labels, never arm, automatic label, or coordinator mapping.
Every unresolved or unreadable selected claim blocks a complete-label point
estimate; there is no silent complete-case deletion, machine-label fill, or
outcome-driven replacement. Human-final is an operational reference, not an
infallible ground truth. Record inter-rater disagreement separately. The
prior P5 human audit remains the rubric/independence precedent; these packets
are a separate sample and must not be mixed into its 160/40 agreement estimator.

`scripts/diagnosis_main_paired_pilot.py prepare` reconstructs the sealed
relation source and writes two blind ZIPs, a coordinator-only map, and a
receipt to a **new private directory outside the repo**. `verify` checks the
packet hashes, frozen source bytes, and seeded family draw without a provider
call. `adjudication` builds a blind packet only after both human submissions
exist, with their exact byte hashes locked inside the packet. `analyze` rejects
changed submissions and requires resolution of every disagreement; it writes only an aggregate private
summary with the input hashes. None of these commands sends packets, calls a
provider, changes the 1,024 frozen requests, or upgrades the original P5
result. Human annotation and delivery remain separate actions.
