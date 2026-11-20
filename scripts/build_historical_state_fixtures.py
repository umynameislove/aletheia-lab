"""Project pinned synthetic state evidence or execute a fresh SQLite audit."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import struct
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from uuid import uuid4

_CLOSE_02 = Path("memory/a-close-02-evolving-obligations-2026-11-06/implementation")
_CLOSE_03 = Path("memory/a-close-03-transfer-and-contribution-2026-11-07")
_STRUCTURAL = Path("native/structural-main-02/ordinary")
_INDEPENDENT = Path("verification/structural-independent-02.json")
_INDEPENDENT_SHA = "8fa34f4fc92b4490d52fc5c6b98e6aa6d37e07c9966be3283155f397b64ed6d5"
_W4_PINS = {
    "examples.json": "551c492736acc76a13c2e5035b1880799c2383ee72467f8af9c95c8c5f7ca53e",
    "lifecycle.py": "89e26f11d9493cf4ab10c4570963955b0bce75b13ef453f0318c6b49e18b700e",
    "planner.py": "5e0c9e7099ba2da46b2029d03b1fe58637cc37cdbb8163ab32ba8833de509f4f",
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def payload_bytes(data: object) -> bytes:
    return json.dumps(data, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def read_pinned(path: Path, expected: str) -> bytes:
    data = path.read_bytes()
    require(sha256(data) == expected, f"Source differs from the verified pin: {path.name}")
    return data


def state(namespace: str, generation: str, witness: object) -> dict[str, str]:
    return {"namespace": namespace, "generation": generation, "state_sha256": sha256(payload_bytes(witness))}


def contract(consumer_id: str, allowed_state: dict[str, str]) -> dict[str, object]:
    return {"schema_version": "historical-state-contract/v1", "consumer_id": consumer_id, "allowed_state": allowed_state}


def receipt(offer: dict[str, Any], observed: dict[str, str], mode: str, producer: str,
            source_receipt_sha256: str, **display_facts: object) -> dict[str, object]:
    return {
        "schema_version": "historical-state-receipt/v1",
        "operation_id": offer["operation_id"],
        "request_id": offer["request_id"],
        "consumer_id": offer["consumer"]["id"],
        "execution_mode": mode,
        "producer_version": producer,
        "source_receipt_sha256": source_receipt_sha256,
        "observation": observed,
        **display_facts,
    }


def replay(root: Path) -> dict[str, object]:
    source = root / _CLOSE_03
    verification = json.loads(read_pinned(source / _INDEPENDENT, _INDEPENDENT_SHA))
    require(verification["all_checked_conditions_pass"] is True, "Independent verification did not pass.")
    pins = verification["inputs_sha256"]
    inputs = ("raw.json", "native-events.jsonl", "bundles/A/model.onnx", "bundles/A/weights.bin",
              "bundles/B/model.onnx", "bundles/B/weights.bin")
    verified = {key: read_pinned(source / _STRUCTURAL / key, pins[(_STRUCTURAL / key).as_posix()])
                for key in inputs}
    raw = json.loads(verified["raw.json"])
    events = [json.loads(line) for line in verified["native-events.jsonl"].splitlines()]
    cases = {item["case"]: item for item in raw["cases"]}
    publication = cases["held-A-denied"]["publication"]["body"]
    require(publication in events and publication["version"] == "B", "B publication is unbound.")
    graph_sha = sha256(verified["bundles/B/model.onnx"])
    require(graph_sha == sha256(verified["bundles/A/model.onnx"]), "A/B graph bytes differ.")
    original_contract = {}
    receipts = {}
    for name, version in (("held-A-denied", "A"), ("B-authorized", "B")):
        item = cases[name]
        offer, envelope = item["offer"], item["native"]
        used = envelope["body"]
        require(envelope["status"] == 200 and used in events and used["version"] == version
                and publication["monotonic_ns"] < used["monotonic_ns"],
                "Execution receipt does not match its native event.")
        require(used["operation_id"] == offer["operation_id"] and used["request_id"] == offer["request_id"]
                and used["namespace"] == offer["scope"]["namespace"] and used["graph_sha256"] == graph_sha,
                "Execution does not match its original offer or graph.")
        weight_bytes = verified[f"bundles/{version}/weights.bin"]
        scale, bias = struct.unpack("<ff", weight_bytes[:8]), struct.unpack("<ff", weight_bytes[8:])
        actual_slices = {"scale_sha256": sha256(weight_bytes[:8]), "bias_sha256": sha256(weight_bytes[8:])}
        require(all(used[k] == v for k, v in actual_slices.items()), "Observed tensor slice identities do not match.")
        output = [s * x + b for s, x, b in zip(scale, used["x"], bias, strict=True)]
        require(used["x"] == [1, 1] and used["outputs"] == {
            "scale_used": list(scale), "bias_used": list(bias), "y": output,
        }, "Actual native diagnostics differ from independently decoded tensor bytes.")
        observed = state(used["namespace"], used["generation"], actual_slices)
        if version == "B":
            authority = {"namespace": publication["namespace"], "generation": publication["generation"],
                         **actual_slices}
            allowed = state(authority["namespace"], authority["generation"], actual_slices)
            original_contract = contract(offer["consumer"]["id"], allowed)
            require(all(cases[key]["offer"]["C"] == authority for key in receipts.keys() | {name}),
                    "The original caller contracts differ from the independently pinned B publication and slices.")
        receipts[name] = receipt(offer, observed, "replay", "close03-structural-main-02",
                                 sha256(payload_bytes(envelope)), output=output, graph_sha256=graph_sha,
                                 current_alias=publication["version"])
    require(receipts["held-A-denied"]["output"] == receipts["B-authorized"]["output"],
            "The replay pair no longer holds final output fixed.")
    return {
        "schema_version": "historical-state-fixture/v1",
        "contract": original_contract,
        "cases": receipts,
        "source_sha256": {"independent_verification": _INDEPENDENT_SHA, **{
            key: sha256(value) for key, value in verified.items()
        }},
    }


def fresh(root: Path, witness_path: Path) -> dict[str, object]:
    # Reuse the exact Lifecycle and offer/capture/commit/ACK/query sequence used in W4.
    source = root / _CLOSE_02
    frozen = {name: read_pinned(source / name, expected) for name, expected in _W4_PINS.items()}
    sys.path.insert(0, str(source))
    try:
        from lifecycle import Lifecycle  # type: ignore[import-not-found]
        from planner import digest  # type: ignore[import-not-found]
    finally:
        sys.path.pop(0)
    fixture = json.loads(frozen["examples.json"])[0]
    offer, record = fixture["offer"], copy.deepcopy(fixture["record"])
    run = uuid4().hex
    offer.update(id=f"historical-state-fresh-{run}", operation_id=f"operation-{run}", request_id=f"request-{run}")
    record["binding"].update(operation_id=offer["operation_id"], request_id=offer["request_id"])
    oid = offer["id"]
    with TemporaryDirectory(prefix="historical-state-w4-") as run_dir:
        with Lifecycle(run_dir, "ordinary") as lifecycle:
            offered = lifecycle.offer(offer)
            capture = lifecycle.capture(oid, record)
            commit = lifecycle.commit(oid)
            ack = lifecycle.ack(oid)
        with Lifecycle(run_dir, "ordinary") as reopened:
            answer = reopened.query(oid, now_ns=150)
            row = reopened.server.execute("SELECT record_hash,commit_hash FROM offers WHERE id=?", (oid,)).fetchone()
    require((offered["status"], capture["status"], commit["status"], ack["status"], answer["status"])
            == ("offered", "captured", "committed", "acked", "answered"), "Fresh W4 audit did not complete.")
    require(row is not None and row["record_hash"] == digest(record)
            and row["commit_hash"] == capture["capture_hash"], "Durable capture did not match after reopen.")
    require(answer["answer"]["state"] == record["facts"]["state"]
            and answer["answer"]["authorized"] is True, "Durable answer disagrees with fresh capture or contract.")
    witness = {
        "scope": "Fresh CPU/SQLite W4 with authored qualification input and fixed local test-clock values.",
        "offer": offer, "capture": capture, "commit": commit, "ack": ack, "reopened": answer,
        "record_sha256": row["record_hash"], "source_sha256": _W4_PINS,
    }
    witness_bytes = payload_bytes(witness)
    with witness_path.open("xb") as output:
        output.write(witness_bytes)
    namespace = record["binding"]["worker_id"]
    generation = str(record["binding"]["generation"])
    trusted_namespace = offer["scope"]["worker_id"]
    trusted_generation = str(offer["scope"]["generation"])
    (allowed_hash,) = offer["C"]["allowed_state_hashes"]
    observed = state(namespace, generation, {"stable_hash": answer["answer"]["state"]["stable_hash"]})
    allowed = state(trusted_namespace, trusted_generation, {"stable_hash": allowed_hash})
    return {
        "schema_version": "historical-state-fixture/v1",
        "contract": contract(offer["consumer"]["id"], allowed),
        "cases": {"fresh-sqlite": receipt(offer, observed, "fresh", "close02-w4-sqlite", sha256(witness_bytes))},
        "source_sha256": _W4_PINS,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("research_root", type=Path)
    parser.add_argument("output", type=Path, help="A new output JSON file.")
    parser.add_argument("--fresh", action="store_true", help="Execute a new local W4 SQLite audit.")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output already exists.")
    data = (fresh(args.research_root, args.output.with_suffix(".witness.json")) if args.fresh
            else replay(args.research_root))
    with args.output.open("x", encoding="utf-8") as output:
        output.write(json.dumps(data, ensure_ascii=False, allow_nan=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
