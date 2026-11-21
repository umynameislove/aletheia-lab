# Historical state qualification fixtures

`replay.json` projects two retained execution receipts from the independently
verified synthetic ONNX control pair. Both operations returned
`[4, 3]` with the same graph, advertised alias and external request ID. The
actual session selected for each operation is bound to the observed tensor
slices. The allowed state is established separately from B's publication
event and pinned weight bytes, and checked against both original contracts.

The projection tool verifies the frozen independent-check artifact, exact raw
file, native events, graphs and weight pins. It computes the slice identities
and expected tensor outputs independently of the retained answer. The typed
receipts contain no producer-supplied verdict. `source_receipt_sha256` pins
the canonical JSON of each retained native call envelope. `source_sha256`
keeps the exact input-file hashes.

From this repository, set `THESIS_ROOT` to the containing thesis workspace and
choose fresh output paths:

```sh
python -B scripts/build_historical_state_fixtures.py "$THESIS_ROOT" /tmp/historical-state-replay.json
cmp fixtures/historical-state/v1/replay.json /tmp/historical-state-replay.json
python -B scripts/build_historical_state_fixtures.py "$THESIS_ROOT" /tmp/historical-state-fresh.json --fresh
```

Fresh mode checks the pinned lifecycle implementation, planner and synthetic
fixture before creating new local SQLite stores and unique operation/request
identities. It runs offer, capture, commit and acknowledgement, then reopens
the stores and independently checks the durable capture identity and answer.
The output receipt names the hash of the resulting `.witness.json` file. This
is a new execution of the local audit on fixed synthetic input with controlled
test clock values. The ONNX packets remain historical replays.

In hosted use the operator must pin the typed receipt separately from the
expected contract and bind both to the authorized uploaded source/project.
These fixtures do not authorize any application workspace by themselves.
