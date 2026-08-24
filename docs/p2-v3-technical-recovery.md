# V3.1 technical-failure preservation and recovery basis

## Status of the original attempt

The immutable v3.1 protocol was opened once at execution commit
`1d365f22c133ce5d70d3ac13b465bfb6202d6e50`. The sealed-open marker exists,
so that attempt must never be rerun, deleted, or overwritten. Execution stopped
before an atomic result store, confirmatory closeout, or scientific disposition
was published. This is an interrupted technical attempt, not a positive,
negative, or abstained scientific finding.

The tracked failure receipt binds this statement to the local registration and
sealed-open files by SHA-256. It records the first exception and the complete
four-cell census found by an outcome-blind train/development audit. Verification
is read-only:

```bash
PYTHONPATH=src python scripts/p2_v3_technical_failure.py
```

The command must fail if the registration or marker changed, if a v3.1 result
store appeared, or if the recorded attempt metadata no longer reconciles.

## Numerical root cause

For development records \(i=1,\ldots,n\), calibration minimizes mean binary
log loss

\[
L(\beta)=\frac{1}{n}\sum_i
\left[\log(1+\exp(x_i^T\beta))-y_i x_i^T\beta\right].
\]

The interrupted implementation evaluated the summed objective, gradient, and
Hessian but compared the summed gradient norm with a fixed `1e-8` threshold.
That made the stopping rule depend on development-set size. In the four audited
cells, the summed gradient norms were between `1.08e-8` and `2.73e-7`, while
the corresponding mean gradient norms were between `1.80e-12` and `4.55e-11`.
The solutions were therefore converged under the intended per-record scale.

Three cells then rejected every line-search step because the best candidate
objective differed from the current summed objective by only
`4.55e-13`, at floating-point roundoff scale. One cell exhausted the iteration
budget with a mean gradient norm of `1.80e-12`. No audited cell showed perfect
or quasi separation, and the Hessian condition numbers ranged from `8.83` to
`41.27`; neither statistical non-identifiability nor ill-conditioning explains
the interruption.

## Recovery rule

The recovered solver evaluates the objective, gradient, and Hessian as means.
Scaling both gradient and Hessian by the same positive factor preserves the
Newton direction, but gives the `1e-8` tolerance a sample-size-invariant
meaning. The original initialization, probability clipping, unregularized
intercept/slope model, line-search schedule, and iteration budget remain
unchanged.

Calibration now has an explicit sum type:

- `ok` contains the converged intercept, slope, and mean-gradient evidence;
- `abstain` contains a reason code and finite diagnostics but no coefficients,
  predictions, loss values, or reusable partial model.

Only a protocol-declared calibration failure is converted to `abstain`.
Invalid provenance, malformed inputs, non-finite model output, and other
technical defects remain hard failures. Dataset execution exposes a fail-closed
entry point that returns the structured abstention instead of terminating the
process.

## Scientific boundary

These changes repair an implementation defect; they do not tune a model,
threshold, seed, dataset split, intervention dose, endpoint, or hypothesis.
They also do not authorize another v3.1 attempt. A future execution requires a
new prospective protocol hash, annotated tag, immutable release, registration,
and single-use sealed marker. The v3.1 receipt must remain part of the
publication record alongside any later result.
