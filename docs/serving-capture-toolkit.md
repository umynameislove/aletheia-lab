# Scoped serving capture toolkit

The evaluation package exposes reusable, framework-neutral primitives rather
than an automatic full execution tracer. The installed serving framework is not
patched. A native adapter supplies the actual selected entry, request association,
public activation/cache history and separately qualified capture-through-use scope.

| Component | Responsibility | Does not establish |
| --- | --- | --- |
| `resident_footprint.capture_entry` | Bounded passive graph from an acquired Python entry | Full dependency execution, native weights, host attestation |
| `resident_state_capture.capture_components` | Typed fingerprints of explicitly acquired supported values | Automatic closure or proof of use |
| `serving_capture_adapter.observe_entry` | Before/invocation/after observations with disjoint timers | Immutability, atomicity, selected-entry use |
| `serving_contract_evidence.resolve_queries` | Existing ordinary joins over qualified lawful records | Truth of source receipts or a new checker advantage |

## Adapter interface

```python
from aletheia_lab.evaluation.serving_capture_adapter import observe_entry

# Acquire the actual selected entry from the serving request, not a disk path.
selected_entry = acquired_model.evaluate

async def invoke(entry):
    # A real adapter performs its actual invocation here. This example is local.
    return entry(actual_operand)

observation = await observe_entry(
    selected_entry,
    invoke,
    package_prefixes=("owned_application",),
    request_id=actual_request_id,
    generation=lambda: observed_activation,
)
```

The callback receives the selected entry, but the toolkit cannot prove that the
callback consumed it. Source/use/reference qualification remains the adapter's
responsibility. No expected state, fault-selected component list or evaluator
reference enters this API. Unsupported objects remain partial/unknown through
the unchanged graph rule. Budget limits are not increased for large models.

`EntryObservation` preserves invocation failure class and acquired observations;
it does not include private exception text. Post-capture failure does not erase
an already completed invocation, including a successful `None` return. Initial
capture errors prevent invocation and propagate to the controller's census.
Cancellation propagates; cancelling a Python task does not prove native SDK work
stopped. The owning controller must archive partial events and verify owned
request/process cleanup before reusing a context.

## Qualification and concurrent update

Keep lawful records, authorized policy and private reference separate. Before
positive identity claims, independently establish owned source, operand types,
selected request-to-instance/binding association, declared dependency coverage
and the whole capture-through-use discipline. The observation's two verification
flags deliberately remain false. They are not inputs to set true after a match;
provide actual qualification evidence to the existing consumer instead.

A model ID, actor PID, object ID, config ACK, equal generation tokens or equal
endpoint hashes do not prove a state snapshot. An in-place/ABA mutation can occur
between measurements and actual consumption. Observe mutable operands as immutable
primitive/snapshot bytes at the actual read; never store an array/list alias as
a reference. A strong baseline may already reconstruct state from lawful ordered
activation/use history. Give that same evidence to both ordinary and candidate.

For overlap, witness native entry/exit and public update intervals on one qualified
monotonic clock. Client futures alone are not actual execution overlap. Keep every
allocated request, failure and unreached cell; do not retry to obtain overlap.
Callback tests are not native bridge or concurrency results. Separately sealed
native adapters supplied the [bounded transfer results](serving-contract-boundary-development.md);
those study-specific bridges and private receipts are not bundled in this toolkit.
Their qualification does not certify a new adapter or arbitrary serving state.

## Cost and scope

Before/invocation/after timers are disjoint. Invocation is a callback interval, not
necessarily kernel-only inference time. Native load, hashing, signature verification,
write/commit-ACK and query/recovery require separate adapter timers. Do not sum
nested timers or interpret smaller storage as lower serving latency.

ONNX Runtime sessions and native resident weights are opaque to the generic Python
graph. Verified artifact acquisition can support a narrower provenance query under
an explicit no-reset/closure/use policy, but it is not a live-weight readout.
Reference and numerical output equality must not fill a missing identity witness.

Tests exercise local API, mutation/ABA, failure, cancellation and generation changes.
They are not native serving, full framework transfer, concurrency throughput,
crash-durability, hostile-host completeness, optimizer superiority or global cost
minimum evidence. No historical study result is changed by this packaging.
