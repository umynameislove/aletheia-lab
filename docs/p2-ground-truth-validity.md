# Ground-truth validity in controlled ML diagnosis

An applied intervention is not automatically a harmful cause, and a harmful
controlled intervention is not automatically identifiable from the observations
available to a diagnoser. The new method audit separates these questions without
changing any historical P2 admission result.

## Three evidence gates

| Gate | Required measurement | Failure disposition |
| --- | --- | --- |
| Fidelity | Actual input/model/training-target change matches the declaration | Invalid intervention |
| Consequence | Paired log-loss increase, executed repair and sham controls | Nonharmful or unverified effect |
| Identifiability | Whole visible payload distinguishes finite declared rivals | Ambiguous or unidentified |

The implementation is `benchmark/p2/ground_truth_validity.py`. It routes supplied
evidence; it does not authenticate the host, establish exhaustive real-world rival
coverage, or promote a case into the registered causal-ground-truth inventory.

## Distribution and sampling are different constructs

The historical study-design phrase “preserving the empirical P(X|Y) by
construction” is too strong for non-neutral within-class replacement sampling.
The historical protocol disclosed replacement. The pool remains the generating
law, but each realized sample generally has different multiplicities. Furthermore,
the historical diagnostic compares development data against a resampled sealed
pool, not merely that pool against its own generator. Historical files/results/
source pins stay immutable; this note is an additive interpretation correction.

Use one of three explicitly different checks:

- Discrete empirical TV and full weighted biased RBF-MMD describe realized
  feature measures. Uniform replication preserves them; unequal multiplicities
  can change them. TV of disjoint continuous empirical samples can be 1 even
  under a common population law. It is not a population shift test.
- Fixed-pool Monte Carlo tests compatibility with independent uniform draws
  from that known pool, conditioning on the pool and target count. It does not
  test unknown development/target population equality.
- Iid permutation tests require a declared independent acquisition contract and
  no known shared/repeated draw ancestry. Equal feature values alone are allowed.
  New code uses a fixed bandwidth and permutation-invariant full statistic.
  Passing provenance guards does not prove the independence declaration true.

Exact class weighting is the simpler ordinary way to change class prior while
preserving the empirical measure within each class. It is not a new estimator.
These distinctions follow the sampling conditions in [Gretton et al., Problem 1
and Lemma 14](https://www.jmlr.org/papers/volume13/gretton12a/gretton12a.pdf)
and the distinction between null-law Monte Carlo and permutation invariance in
[Hemerik and Goeman, section 3.1](https://link.springer.com/article/10.1007/s11749-017-0571-1).

## Executed controlled method transfer

Development used bundled Iris: 35 cells. Reserved-source transfer used Wine and
Digits: 40 cells per source, five fixed splits, eight conditions each. The training-
target cyclic recoding operator was withheld from development. All 80 transfer
cells completed; none was excluded after outcomes. The design and three execution
files were locally sealed and checked before transfer. Dataset hashes and package
versions were recorded during execution; this is not external preregistration or
source/document-blind validation.

| Transfer condition | Cells | Prediction-only disposition | Configured lineage disposition |
| --- | ---: | --- | --- |
| Input sign change | 10 | Harmful, ambiguous | Supported within catalogue |
| Resident coefficient sign change | 10 | Harmful, ambiguous | Supported within catalogue |
| Training-target recoding | 10 | Supported within catalogue | Supported within catalogue |
| Sham | 10 | Not harmful | Not harmful |
| Unused metadata change | 10 | Not harmful | Not harmful |
| Two sign changes cancel | 10 | Not harmful | Not harmful |
| Declared change not applied | 10 | Invalid intervention | Invalid intervention |
| Wrong repair: reload model, leave input faulty | 10 | Unverified repair | Unverified repair |

Ten input/resident twin pairs have equal actual probability buffers and equal
complete prediction-only payloads, despite different intervention sites. Their
configured lineage differs. The impossibility is relative to that declared view:
an observation-only reader cannot identify the hidden site from equal bytes.
The two views are dependent observations of the same cells, not replication N.

Configured lineage is deliberately authored instrumentation of actual inputs,
model parameters, training targets and clean references. Ordinary access to the
same sufficient lineage solves the same finite distinction. This is not a measured
advantage over a strong diagnoser, proof default SDK logs are enough, or a new
causal-discovery algorithm. A finite singleton does not rule out unenumerated causes.

The independent numerical check reconstructed all 80 transfer records with separate
fits and direct coefficient/intercept forward computation. Prediction/lineage
payloads, source/split hashes and effect/repair/sham values matched. This verification
is not a second independent validation cohort.

## Reproduction

Use the repository dependencies; the recorded run used scikit-learn 1.9.0 and
NumPy 2.4.6. From the repository root, with `PYTHONPATH=src`, run the entrypoint
`scripts/p2_ground_truth_validity.py`:

1. `design --output <fresh-directory>/seal.json` records the prospective design/code.
2. `development --output <fresh-directory>/development-reviewed.json` executes Iris.
3. `sampling --output <fresh-directory>/sampling.json` executes the known-law checks.
4. `transfer --expected-design-sha256 <recorded-digest> --seal <fresh-directory>/seal.json --output <fresh-directory>/transfer.json` checks the seal before execution.
5. `scripts/analyze_p2_ground_truth_validity.py <fresh-directory>` rebuilds tables,
   rejects missing/duplicate census entries, and verifies payload/rival decisions.

Output files are create-only. Re-execution in a fresh directory is reproduction,
not permission to tune a method on the original reserved outcome and claim the
same source untouched. No provider, human label or historical registered store is
needed. Default pytest includes unit and local integration regressions; the
evaluation profile also lists them explicitly.

## Thesis and publication implications

Keep the contribution as a reproducible construct-validity result: fidelity,
controlled consequence, sampling assumptions and evidence-relative diagnosis must
not be collapsed into an injected fault label. [Causal Testing Framework](https://arxiv.org/html/2209.00357)
already studies causal-effect oracles; [MuFF](https://arxiv.org/html/2501.09846)
already studies mutation stability and sensitivity. [BBSE](https://proceedings.mlr.press/v80/lipton18a/lipton18a.pdf)
already requires conditional invariance, support and invertibility. These checks
instantiate and test relevant limits; none of those generic ideas is claimed new.

The bounded method audit supports the thesis. A standalone paper needs a stronger
external validity/contribution case; 80 authored public-data cells alone do not
provide real deployment prevalence, unrestricted ground truth, human usefulness,
LLM diagnosis efficacy, or a Q1 acceptance guarantee. Historical causal-diagnosis
denominator remains zero under the current downstream policy.
