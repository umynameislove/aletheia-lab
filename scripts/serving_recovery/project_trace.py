"""Project actual observations without copying driver targets or private reference."""

import argparse
import json
from pathlib import Path

STATE = ("actualcomponents", "sourcehashes", "generation", "pid", "instance_id", "fitted_id")
RECORD = (
    "input",
    "output",
    "fitted_output",
    "selected_ns",
    "consumed_ns",
    "completed_ns",
    "evidence_origin",
)
HTTP = ("status", "method", "route", "parent_start_ns", "parent_end_ns")
COMPONENTS = (
    "coef",
    "intercept",
    "n_features_in",
    "helper",
    "runtime_offset",
    "runtime_revision",
    "helper_revision",
    "training_revision",
)
SOURCES = ("runtime", "helper", "training")


def scalar(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ValueError("Expected a scalar observation, not an embedded object")


def project(rows):
    records, ids = [], set()
    for row in rows:
        rid = row["request_id"]
        if not isinstance(rid, str) or not rid or rid in ids:
            raise ValueError("Nonempty unique request IDs required")
        ids.add(rid)
        http = row.get("http")
        if not isinstance(http, dict):
            raise ValueError("Every offered row requires an observed HTTP response")
        if http["status"] != 200:
            state, record = {}, {"response_unavailable": True}
        else:
            observed = http["body"]["parameters"]["record"]
            if observed["request_id"] != rid or any(observed[k] != row[k] for k in STATE + RECORD):
                raise ValueError("Driver and public response binding differs")
            state = {
                k: scalar(observed[k])
                for k in STATE
                if k not in ("actualcomponents", "sourcehashes")
            }
            components = observed["actualcomponents"]
            state["actualcomponents"] = {
                k: [scalar(v) for v in components[k]] if k == "coef" else scalar(components[k])
                for k in COMPONENTS
            }
            state["sourcehashes"] = {k: scalar(observed["sourcehashes"][k]) for k in SOURCES}
            record = {k: scalar(observed[k]) for k in RECORD}
        record["http"] = {k: scalar(http[k]) for k in HTTP if k in http}
        records.append({"id": rid, "state": state, "record": record})
    if not records:
        raise ValueError("No response records")
    # Reject NaN/Infinity instead of publishing a non-canonical representation.
    json.dumps(records, allow_nan=False)
    return {"schema": "serving-observed-projection/v1", "records": records}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    raw = args.input.read_bytes()
    if len(raw) > 4 * 1024**2:
        parser.error("Bounded local trace exceeds 4 MiB")
    value = project(json.loads(raw))
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write("\n")
