# Label-noise confirmatory results

> Status: registered confirmatory study complete; full result store verified and
> preserved by content address. This summary is publication-facing and does not
> replace the immutable result artifacts.

## Registration and execution

The study followed the frozen protocol in
[`p2_label_noise_confirmatory_protocol.json`](../configs/benchmark/p2_label_noise_confirmatory_protocol.json),
SHA-256
`1a7340d0897fcbbde02bb0a3ffe0a50cccd1cebd695ebf2c293c6c260bb02d4e`.
The protocol was published as the immutable GitHub release
[`p2-label-noise-confirmatory-v2`](https://github.com/umynameislove/aletheia-lab/releases/tag/p2-label-noise-confirmatory-v2)
before the sealed outcomes were opened.

The complete registered batch ran at commit
`81abe13da1cf6072396bb73289569f95d64b520f` and released both dataset
outcomes atomically. It reconciled 180 replicates for the Telco primary study
and 180 replicates for the UCI Bank Marketing external replication. Every
technical and control gate passed.

The tracked machine-readable publication receipt is
[`p2_label_noise_confirmatory_v2_closeout_receipt.json`](../configs/benchmark/provenance/p2_label_noise_confirmatory_v2_closeout_receipt.json).
The full artifacts are intentionally excluded from Git because they contain
approximately 699 MB of per-record and per-replicate evidence.

## Registered decision

| Decision field | Result |
|---|---|
| Primary Telco gate | Pass |
| External Bank replication | Fail |
| Label-noise mechanism admitted | Yes |
| Cross-dataset claim allowed | No |
| Disposition | `primary_dataset_bounded_admission` |

The result admits label-noise mechanism coverage only for the registered Telco
dataset, logistic-regression model, class-conditional corruption construct and
probabilistic endpoint. The external replication cannot be reported as a pass
and cannot support a cross-dataset claim.

## Co-primary direction results

| Dataset | Direction | Relative log-loss change | 95% crossed-bootstrap interval | Holm-adjusted p | Registered result |
|---|---|---:|---:|---:|---|
| Telco | `yes_to_no` | +5.651% | [+2.923%, +8.408%] | 0.000020 | Pass |
| Telco | `no_to_yes` | +35.027% | [+27.502%, +43.169%] | 0.000020 | Pass |
| Bank | `yes_to_no` | +5.327% | [+0.332%, +10.778%] | 0.057779 | Fail |
| Bank | `no_to_yes` | -77.168% | [-79.229%, -74.899%] | 1.000000 | Fail |

Telco satisfied the practical-effect, interval, multiplicity and technical
requirements in both co-primary directions. Both Telco dose series increased
monotonically in the registered degradation direction.

For Bank `yes_to_no`, the practical-effect and interval requirements passed,
and mean accuracy change was exactly zero at every registered dose. The raw
one-sided p-value was `0.028890`, but the Holm-adjusted value was `0.057779`, so
the direction remains a registered failure. This is descriptive evidence that
the probability endpoint can expose degradation hidden by accuracy; it is not
a successful external replication.

For Bank `no_to_yes`, log loss changed strongly in the opposite direction from
the registered degradation hypothesis. Because all technical controls passed,
this outcome requires an outcome-aware analysis of class prevalence, temporal
shift, calibration and corruption direction. Such analysis is exploratory and
cannot modify this registered decision.

## Dose summaries

| Dataset | Direction | 10% | 20% | 30% |
|---|---|---:|---:|---:|
| Telco | `yes_to_no` | +0.447% | +2.378% | +5.651% |
| Telco | `no_to_yes` | +6.260% | +18.691% | +35.027% |
| Bank | `yes_to_no` | +1.904% | +3.470% | +5.327% |
| Bank | `no_to_yes` | -54.440% | -68.400% | -77.168% |

Lower-dose cells are descriptive dose-response evidence and cannot rescue or
overturn either co-primary decision.

## Allowed interpretation

The registered result supports the following bounded claim:

> Under the registered Telco dataset, logistic-regression model and
> class-conditional label-noise conditions, the construct-aware probabilistic
> gate detected degradation that an accuracy-only comparator can understate.

The result does not establish universal superiority over accuracy, robust
learning methods, other benchmark systems or diagnostic agents. It does not
authorize a cross-dataset claim. The Bank findings must be retained as a failed
replication and may motivate only a separately registered follow-up study.

## Artifact preservation

The verified result store is identified by:

```text
result_store_sha256 = 7e46d0997bc5ad6807409a4aebea39c82c11216f2bdcadd5704724994117504c
closeout_sha256     = cf89cce144760110958fd4aebb774448bdd0adac71572c79a4ea196851c14f02
```

The content-addressed preservation copy was verified using the same fail-closed
store verifier as the original. The copy is maintained read-only outside the
Git working tree. A durable publication archive URI must be added to the
machine-readable receipt when institutional or public archival storage is
selected; adding an archive location must not alter or regenerate the result
store.
