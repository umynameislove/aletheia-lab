"""Analysis-only strengthening of native controls on immutable executed data.

Source-contract implications are not materialized per-request actual-use proofs.
Neither delayed SQLite receipt nor client abandonment implies missing native
capability. This addition does not replace the prospectively frozen analysis.
"""

from __future__ import annotations

import json
import math
import statistics
from collections import Counter
from pathlib import Path
from typing import Any

from aletheia_lab.evaluation.litserve_evidence_analysis import COEFFICIENTS, _scores
from aletheia_lab.evaluation.litserve_evidence_study import verify
from aletheia_lab.evaluation.model_load_retention import encode
from aletheia_lab.filesystem import write_new_file
from aletheia_lab.project.identity import content_sha256


def native_response_contract(
    row: dict[str, Any], *, synchronous_immutable_contract: bool
) -> dict[str, str]:
    """Use client evidence + explicit trusted deployment/source assumptions only.

    Requires direct nonstreaming HTTP, immutable correct setup, no background
    inference or proxy/cache, and the pinned default ordered/cardinality-checked
    batch loop. These premises would be invalid in several old fault studies.
    No raw reference event, collector receipt or future worker terminal is used.
    """
    result = {
        "origin": "unknown" if row["status"] == 200 else "unavailable",
        "closure": "unknown",
    }
    if not synchronous_immutable_contract or row.get("error"):
        return result
    body = row.get("body")
    if row["status"] == 200 and type(body) is dict:
        value = body.get("output")
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            expected = COEFFICIENTS[row["endpoint"]] * row["x"] + 1
            if math.isclose(value, expected, abs_tol=1e-12, rel_tol=1e-12):
                result["origin"] = "compliant"
        result["closure"] = "closed"
    elif (
        row["status"] == 504
        and type(body) is dict
        and body.get("detail")
        in {
            "Request timed out",
            "cooperative deadline expired before designated computation",
        }
    ):
        result["closure"] = "closed"
    return result


def compare_source_contract(source: dict[str, Any], analyzed: dict[str, Any]) -> dict[str, Any]:
    rows, truths = source["rows"], analyzed["rows"]
    if [r["token"] for r in rows] != [r["token"] for r in truths]:
        raise ValueError("reference and client census differ")
    answers = [native_response_contract(r, synchronous_immutable_contract=True) for r in rows]
    return {
        "origin": _scores([r["truth"]["origin"] for r in truths], [a["origin"] for a in answers]),
        "closure": _scores(
            [r["truth"]["closure_at_client_cut"] for r in truths],
            [a["closure"] for a in answers],
        ),
    }


def _costs(sources: list[dict[str, Any]]) -> dict[str, Any]:
    names = (
        "capture_payload_bytes",
        "capture_recorded_ns",
        "collector_persistence_ns",
        "collector_scan_ns",
        "raw_and_log_physical_bytes",
        "sqlite_physical_bytes",
        "raw_read_ns",
        "analysis_ns",
        "unmeasured_final_stats_writes",
    )
    costs = {
        key: {
            "sum": sum(s["costs"][key] for s in sources),
            "median": statistics.median(s["costs"][key] for s in sources),
            "min": min(s["costs"][key] for s in sources),
            "max": max(s["costs"][key] for s in sources),
        }
        for key in names
    }
    for key in ("verification_ns", "link_physical_bytes"):
        values = [s["signed_baseline"][key] for s in sources]
        costs["signed_bundle_" + key] = {
            "sum": sum(values),
            "median": statistics.median(values),
            "min": min(values),
            "max": max(values),
        }
    return costs


def lawful_status_counts(sources: list[dict[str, Any]]) -> dict[str, Any]:
    """Validate the fixed six lawful offers per cell before summarizing."""
    for source in sources:
        if sum(r["family"] == "ordered_endpoint_batches" for r in source["rows"]) != 6:
            raise ValueError("lawful planned census differs")
    return {
        arm: dict(
            Counter(
                str(r["status"])
                for s in sources
                if s["arm"] == arm
                for r in s["rows"]
                if r["family"] == "ordered_endpoint_batches"
            )
        )
        for arm in ("native", "cooperative")
    }


def closeout(execution_root: Path, directory: Path) -> dict[str, Any]:
    """Read-only original replay first; write one separately labelled analysis.

    execution_root is the retained byte-identical execution implementation, not
    a resealed current implementation. No serving, signing or provider execution.
    """
    replay = verify(execution_root, directory)
    if not replay["complete"]:
        raise ValueError("complete native census required for transfer closeout")
    original = json.loads((directory / "results.json").read_bytes())
    sources = [
        json.loads((directory / f"cell-{i:02d}" / "assessed-source.json").read_bytes())
        for i in range(len(original["cells"]))
    ]
    cells = [
        compare_source_contract(s, c["analysis"])
        for s, c in zip(sources, original["cells"], strict=True)
    ]
    comparator = {
        endpoint: {
            metric: sum(cell[endpoint][metric] for cell in cells)
            for metric in ("denominator", "correct", "false_conclusive", "unknown", "unavailable")
        }
        for endpoint in ("origin", "closure")
    }
    lawful = lawful_status_counts(sources)
    report = {
        "schema": "litserve-native-control-closeout/v2",
        "analysis_correction": "v1 added native semantic control correctly but mislabeled lawful family as batch-routing, giving empty lawful counts; retain v1, use validated fixed six-offer census here.",
        "analysis_only_post_result_strengthening": True,
        "original_plan_sha256": replay["plan_sha256"],
        "original_results_sha256": replay["results_sha256"],
        "analysis_code_sha256": content_sha256(Path(__file__).read_bytes()),
        "original_replay": "pass_including_real_public_signature_verification",
        "source_contract_native_response": comparator,
        "per_cell_source_contract": cells,
        "lawful_batch_status_counts": lawful,
        "instrumented_costs": _costs(sources),
        "native_runtime_reruns": 0,
        "provider_calls": 0,
        "disposition": "bounded_transfer_no_new_checker_or_optimizer_advantage",
        "interpretation": {
            "baseline": "Original native_uid is persisted-native-UID tier; this stronger control uses client response and source-contract implications before SQLite receipt.",
            "assumptions": "Direct synchronous nonstreaming HTTP; immutable correct endpoint setup; no hidden background inference, cache, proxy or dispatch mutation; pinned ordered/cardinality-checked native batching. Conditional provenance is not a materialized actual-use certificate or host attestation.",
            "abandonment": "ReadTimeout leaves source-contract closure unknown; only retained actual-entry/terminal chronology establishes open-at-client-cut retrospectively.",
            "repair": "Application-level cooperative pre-computation deadline refusal; not native active timeout, preemption, success gain or guarantee of HTTP deadline. Refusing a blocker can change queue outcomes. All refusals and the lawful queue timeout remain included.",
            "cost": "Instrumented encoding/IO/query/persistence only; scan includes repeated file reads; raw_read_ns includes final client/receipt archival IO plus journal read; signed verification_ns includes signing and verification. Final stats update per PID omitted. No uninstrumented overhead, native optimal cost or production latency frontier established.",
            "independence": "Previously unused implementation, one controlled CPU affine deployment, nested processes/requests; not eight independent applications or natural incidents.",
            "forecast": "Frozen 36 nested checks preserved; post-result stronger comparator is not a new prospectively validated forecast or checker win.",
        },
    }
    report["closeout_sha256"] = content_sha256(encode(report).encode())
    path = directory / "native-control-closeout-v2.json"
    raw = encode(report).encode()
    if path.exists():
        if path.read_bytes() != raw:
            raise ValueError("analysis changed; preserve previous closeout separately")
    else:
        write_new_file(path, raw)
    return report
