# P2 label-noise shift v3.3 results and preservation

## Registered result

The single registered v3.3 attempt completed both datasets and released their
outcomes atomically. The terminal disposition is `ABSTAIN`; it is a scientific
closeout, not a technical failure.

- Protocol SHA-256:
  `5fa057e17203b00fa78fc86d58ce5b324bb30cebf916ebb94a3d6b389f30b456`
- Terminal-store SHA-256:
  `d2a4537de7f25a069cd23c7942d0e3d3cef9c6e4fea826a7080d61a04f95f152`
- Terminal-artifact SHA-256:
  `9b8d87cbd3e52dc5c6da50066c6816d5620457a3d8ac8094fafc8136560339c4`
- Execution commit: `8155a31a10a8749fb0ea2c299e823eb10c4f3760`
- Cross-dataset claim allowed: no
- Rerun allowed: no

Both registered directions had Holm-adjusted p-values
`0.00001999980000199998`. This is strong directional signal, but it is not
admission: the prespecified cross-environment assumption family passed at odds
multiplier 1.0 and failed at the extreme 0.25 and 4.0 environments. The correct
scientific status is therefore `assumption_limited`.

## Claim boundary

Allowed reporting says that the registered two-dataset study detected strong
directional signal while the extreme-prior assumption checks prevented a
cross-dataset claim. It may be used to evaluate whether the system abstains and
explains the evidence boundary correctly.

Forbidden reporting calls label noise confirmed or admitted, claims broad
cross-dataset generalization, treats two datasets as independent replications of
different protocols, or says no effect was observed.

## Content-addressed preservation

The approximately 101 MB terminal store is excluded from Git. Its verified,
read-only copy is addressed outside the checkout as:

```text
preserved-artifacts/
  p2-label-noise-shift-factorial-v3.3/
    sha256-d2a4537de7f25a069cd23c7942d0e3d3cef9c6e4fea826a7080d61a04f95f152/
```

That directory contains the byte-identical terminal store, preflight registration,
sealed-open receipt, and preservation receipt. The compact tracked summary is
`configs/benchmark/provenance/p2_label_noise_shift_v3_3_publication_summary.json`.
It is not a replacement for the external evidence.

Preserve once or verify later without model fitting:

```bash
PYTHONPATH=src python scripts/p2_v3_3_preservation.py preserve
PYTHONPATH=src python scripts/p2_v3_3_preservation.py verify
```

Neither command imports or calls the execution entrypoint. Preservation refuses
unexpected files, symlinks, altered hashes, inconsistent registration/marker
bindings, or a content address different from the immutable terminal-store root.
