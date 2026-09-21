# Qwen3.8 calibration failure closeout

## Terminal disposition

The corrected Qwen3.8 Q8 development calibration is closed as
`operationally_infeasible`. This is the prespecified failure branch, not a
passing calibration and not evidence about the primary GPT-4.1 study.

The exact Q8 artifact and pinned `llama.cpp` Metal build loaded successfully on
the loopback-only server. Four serial inference tasks completed without
truncation. The fourth response then reached a terminal `QwenCalibrationError`
before a calibration receipt could be published. The deterministic request
order locates that boundary at the `devcase-conflicting-signals` `A3` cell. The
CLI did not expose the inner validation exception and raw response text was not
retained, so the closeout does not claim which field or semantic check failed.

The private server log and operator transcript remain outside Git. The tracked
closeout binds only their byte counts and SHA-256 identities. It contains no
absolute local path, operator identity, prompt text, response text, protected
outcome, or item-level main-study data.

## Scientific consequence

The frozen Qwen candidate required all six development cells and one
repeatability call to pass before the 72-request sensitivity could run. Its
failure rule forbade model, quantization, mode, prompt, schema, sampling, or
answer-quality selection after an observed failure. Therefore:

- the four observed development calls are retained as terminal calibration
  evidence rather than promoted to a scientific result;
- the 72-request Qwen census remains frozen but unexecuted;
- Qwen is reported as an operationally infeasible secondary robustness study;
- Qwen contributes no primary denominator, pooled estimate, cross-model
  comparison, or superiority claim; and
- any future Qwen recovery requires a separate registration and cannot rewrite
  this closeout.

The sealed 32-family, 128-context, 1,024-request GPT-4.1 design, main response
contract, metrics, aggregation, missingness, multiplicity, analysis plan, and
one-shot execution semantics are unchanged. Protected main outcomes remain
closed and no registered main attempt was consumed.

## Readiness consequence

The machine-verifiable closeout, frozen contract bindings and outcome-blind
repository audits are sufficient to close the optional Qwen branch and the P4
readiness work. An additional reviewer signature is not required because it
would add process without changing the frozen design or its validity evidence.
This readiness finding does not itself start the registered main attempt; that
separate execution still requires an explicit operator action because it opens
protected outcomes and consumes the one-shot attempt.
