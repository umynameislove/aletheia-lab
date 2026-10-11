"""Read-only raw reference for source-informed native serving replication.

No candidate resolver, model deserialization, socket, signing or native rerun.
This checks a trusted adapter's actual objects and operands, not host attestation.
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Any

from aletheia_lab.evaluation.model_load_retention import encode
from aletheia_lab.evaluation.request_model_source import planned_rows
from aletheia_lab.project.identity import content_sha256


def digest(value: Any) -> str:
    return content_sha256(encode(value).encode())


def check_source(source: dict[str, Any], version: str, adapter_sha256: str) -> list[str]:
    if source["planned"] != planned_rows(source["mode"]):
        raise ValueError("native source changed the offered request census")
    if [row["token"] for row in source["rows"]] != [row["token"] for row in source["planned"]]:
        raise ValueError("native outcome omitted or reordered planned requests")
    environment = source["environment"]
    if environment is None:
        return ["unknown"] * len(source["planned"])
    if environment["packages"]["ray"] != version or environment["adapter_sha256"] != adapter_sha256:
        raise ValueError("executed native environment or adapter differs from design")
    for row, planned in zip(source["rows"], source["planned"], strict=True):
        if any(row[key] != value for key, value in planned.items()):
            raise ValueError("request authority or input changed")
    check_loads(source)
    return [reference_row(source, row) for row in source["rows"]]


def check_loads(source: dict[str, Any]) -> None:
    for load in source["loads"].values():
        if load["object_fingerprint"] != digest(load["parameters"]):
            raise ValueError("native object parameters differ from recorded identity")
        if source["mode"] == "original":
            if load["parameters"] != {"value": f"model_obj_for_{load['model']}"}:
                raise ValueError("original model object changed")
        else:
            artifact = source["artifacts"][load["model"]]
            raw = bytes.fromhex(load["raw_hex"])
            if (
                raw != bytes.fromhex(artifact["raw_hex"])
                or content_sha256(raw) != artifact["artifact_sha256"]
                or load["parameters"] != artifact["parameters"]
                or load["object_fingerprint"] != artifact["object_fingerprint"]
            ):
                raise ValueError("actual loaded bytes/object differ from owned model release")


def members(source: dict[str, Any], row: dict[str, Any]) -> list[tuple[dict[str, Any], int]]:
    batches = [item for item in source["batches"] if item["scenario"] == row["scenario"]]
    if source["mode"] == "original":
        rows = [item for item in source["rows"] if item["scenario"] == row["scenario"]]
        if len(batches) != len(rows) or any(
            len(item["members"]) != 1 or not item["completed"] for item in batches
        ):
            return []
        if any(item["http_status"] != 200 for item in rows):
            return []
        return [(batches[row["ordinal"]], 0)]
    return [
        (batch, index)
        for batch in batches
        for index, item in enumerate(batch["members"])
        if item["token"] == row["token"]
    ]


def reference_row(source: dict[str, Any], row: dict[str, Any]) -> str:
    candidates = members(source, row)
    if len(candidates) > 1:
        return "conflict"
    if row["http_status"] != 200 or not candidates:
        return "unknown"
    batch, index = candidates[0]
    if not batch["completed"]:
        return "unknown"
    load = source["loads"].get(batch["actual_generation"])
    if load is None:
        return "unknown"
    member = batch["members"][index]
    observed = batch["actual_object"]
    if any(
        observed[key] != load[key]
        for key in ("object_id", "pid", "object_fingerprint", "parameters")
    ):
        raise ValueError("inference used a different object from its declared load generation")
    if member["arg"] != row["arg"] or member["output"] != row["output"]:
        return "conflict"
    if source["mode"] == "original":
        expected = f"Response from {load['parameters']['value']} {row['arg']}"
    else:
        parameters = load["parameters"]
        expected = parameters["coef"][0] * float(row["arg"]) + parameters["intercept"]
    if expected != row["output"]:
        raise ValueError("recorded prediction differs from independently recomputed object result")
    return "compliant" if load["model"] == row["requested_model"] else "violation"


def output_baseline(source: dict[str, Any], row: dict[str, Any]) -> str:
    if row["http_status"] != 200:
        return "unknown"
    if source["mode"] == "original":
        candidates = [
            model
            for model in ("aaa", "bbb")
            if row["output"] == f"Response from model_obj_for_{model} {row['arg']}"
        ]
    else:
        if type(row["output"]) not in {int, float} or not math.isfinite(row["output"]):
            return "unknown"
        candidates = [
            model
            for model, artifact in source["artifacts"].items()
            if artifact["parameters"]["coef"][0] * float(row["arg"])
            + artifact["parameters"]["intercept"]
            == row["output"]
        ]
    if len(candidates) != 1:
        return "unknown"
    return "compliant" if candidates[0] == row["requested_model"] else "violation"


def native_context_baseline(source: dict[str, Any], row: dict[str, Any]) -> str:
    """Use fixed native batch contexts where present, not just the scalar context.

    This diagnoses context/selection consistency, not actual model consumption.
    A native claimed model matching ingress is not an inference attestation.
    """
    if row["http_status"] != 200 or not row.get("response_request_id"):
        return "unknown"
    candidates = [
        item["model"]
        for batch in source["batches"]
        for item in batch.get("native_batch_contexts", [])
        if item["request"] == row["response_request_id"]
    ]
    if not candidates:
        candidates = [
            batch["native_context"]["generic_model"]
            for batch in source["batches"]
            if batch["native_context"]["request"] == row["response_request_id"]
        ]
    if len(candidates) != 1 or candidates[0] not in {"aaa", "bbb"}:
        return "unknown"
    return "compliant" if candidates[0] == row["requested_model"] else "violation"


def raw_analysis(source: dict[str, Any], truth: list[str]) -> dict[str, Any]:
    output = [output_baseline(source, row) for row in source["rows"]]
    native = [native_context_baseline(source, row) for row in source["rows"]]
    return {
        "reference": truth,
        "reference_counts": dict(Counter(truth)),
        "output_only": output,
        "output_only_counts": dict(Counter(output)),
        "native_context": native,
        "native_context_counts": dict(Counter(native)),
        "batch_member_counts": dict(
            Counter(str(len(batch["members"])) for batch in source["batches"])
        ),
        "native_failure_count": len(source["failures"]),
        "http_failure_count": sum(row["http_status"] != 200 for row in source["rows"]),
    }
