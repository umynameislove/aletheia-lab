"""Hash-only reference reconstruction and fixed descriptive validation endpoints."""

from __future__ import annotations

import json
import tempfile
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path
from typing import Any

from aletheia_lab.evaluation.model_load_contract import (
    Record,
    Scope,
    completion_monitor,
    receipt_checker,
)
from aletheia_lab.evaluation.model_load_provenance import document_digest, provenance_checker
from aletheia_lab.evaluation.model_load_validation_lifecycle import observation
from aletheia_lab.project.identity import content_sha256

CUTOFFS = ("before", "after")
METHODS = ("S", "T", "P")


def _schedule(row: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    from aletheia_lab.evaluation.model_load_validation import load_protocol

    protocol = load_protocol(Path(__file__).resolve().parents[3])
    schedule = next(s for s in protocol["schedules"] if s["id"] == row["slot"]["schedule"])
    backend = next(b for b in protocol["backends"] if b["id"] == row["slot"]["backend"])
    return schedule, backend


def _project_records(row: dict[str, Any], digests: dict[str, str]) -> list[dict[str, Any]]:
    """Reconstruct frozen transport for comparison, never repair observer inputs."""
    ledger, slot = row["ledger"], row["slot"]
    scope = Scope(**ledger["scope"])
    root = ledger["root_binding"]
    chosen = ledger["selection"]
    records = [root] if slot["planned_auxiliary_entries"] else []
    for entry in ledger["auxiliary_entries"]:
        records.append(
            asdict(
                Record(
                    entry["occurrence"],
                    Scope(**entry["scope"]),
                    "load",
                    entry["sha256"],
                    root["selection"],
                )
            )
        )
    if slot["planned_auxiliary_entries"]:
        records.append(
            asdict(
                Record(
                    "parent-close",
                    Scope(**root["scope"]),
                    "closure",
                    load_count=len(ledger["auxiliary_entries"]),
                )
            )
        )
    records.append(chosen)
    for entry in ledger["target_entries"]:
        record = asdict(
            Record(entry["occurrence"], scope, "load", entry["sha256"], chosen["selection"])
        )
        records.append(record)
        if slot["schedule"] == "delivery-duplicate-only":
            records.append(record)
    if not slot["planned_target_entries"]:
        records.append(asdict(Record("cache", scope, "cache_hit", digest=digests["A"])))
    records.append(
        asdict(Record("close", scope, "closure", load_count=len(ledger["target_entries"])))
    )
    return records


def _transport_counts(row: dict[str, Any], records: list[dict[str, Any]]) -> dict[str, int]:
    ledger, fault = row["ledger"], row["slot"]["schedule"]
    loads = sum(r["scope"] == ledger["scope"] and r["kind"] == "load" for r in records)
    roots = sum(r["scope"] == ledger["root_binding"]["scope"] for r in records)
    return {
        "submitted": len(records),
        "delayed": loads if fault == "retry-reselect-legitimate" else 0,
        "foreign": loads if fault == "retry-inherited-foreign-mailbox" else 0,
        "expired": roots if fault == "retry-root-expired" else 0,
    }


def _visible_records(
    row: dict[str, Any], records: list[dict[str, Any]], cutoff: str
) -> list[dict[str, Any]]:
    ledger, fault = row["ledger"], row["slot"]["schedule"]
    visible = []
    for record in records:
        if fault == "retry-root-expired" and record["scope"] == ledger["root_binding"]["scope"]:
            continue
        target_load = record["scope"] == ledger["scope"] and record["kind"] == "load"
        if target_load and fault == "retry-inherited-foreign-mailbox":
            continue
        if target_load and fault == "retry-reselect-legitimate" and cutoff == "before":
            continue
        visible.append(record)
    return visible


def _validate_observations(row: dict[str, Any], digests: dict[str, str]) -> None:
    ledger = row["ledger"]
    if not ledger["closed"]:
        raise ValueError("observer cutoffs precede authoritative closure")
    records = _project_records(row, digests)
    if document_digest(row["transport"]) != document_digest(_transport_counts(row, records)):
        raise ValueError("observer transport census changed")
    if set(row["observations"]) != set(CUTOFFS):
        raise ValueError("observer cutoff census changed")
    for cutoff in CUTOFFS:
        projected = {
            "contract": ledger["contract"],
            "scope": ledger["scope"],
            "records": _visible_records(row, records, cutoff),
        }
        if document_digest(row["observations"][cutoff]) != document_digest(projected):
            raise ValueError("observer evidence differs from actual frozen delivery")


def _validate_frame(row: dict[str, Any], digests: dict[str, str], backend: dict[str, Any]) -> None:
    ledger, slot = row["ledger"], row["slot"]
    target = ledger["scope"]
    snapshots = ledger["path_snapshots"]
    hashes = {
        name: content_sha256(bytes.fromhex(snapshots[name + "_raw_hex"]))
        for name in ("pre", "post")
    }
    if hashes["pre"] != digests["A"] or hashes["post"] not in digests.values():
        raise ValueError("path snapshots differ from the sealed spool domain")
    messages = ledger["native_messages"]
    if set(messages) != {"stdout", "stderr"} or any(type(v) is not str for v in messages.values()):
        raise ValueError("native messages lack complete stdout/stderr capture")
    manifest = json.dumps(
        {
            "schema": "model-load-manifest/v1",
            "selected_sha256": ledger["selection"]["digest"],
            "backend": slot["backend"],
            "format": slot["api"],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    frame = {
        "backend": slot["backend"],
        "backend_version": backend["version"],
        "policy": ledger["contract"]["policy"],
        "retry_policy": ledger["contract"]["retry"],
        "public_request_id": target["request"],
        "public_root_id": target["request"],
        "public_attempt": target["attempt"],
        "requested_uri": f"spool://{target['request']}/model",
        "selected_version": None,
        "registered_metadata": None,
        "manifest": {"text": manifest.decode(), "sha256": content_sha256(manifest)},
        "pre_path_sha256": hashes["pre"],
        "post_path_sha256": hashes["post"],
        "native_messages": messages,
    }
    if document_digest(row["path_frame"]) != document_digest(frame):
        raise ValueError("path frame differs from actual application snapshots")


def validate_evidence(row: dict[str, Any], digests: dict[str, str]) -> None:
    """Bind candidate evidence to actual snapshots and the frozen transport."""
    if "ledger" not in row:
        if row["status"] == "completed" or "observations" in row or "path_frame" in row:
            raise ValueError("evidence lacks its actual execution ledger")
        return
    ledger, slot = row["ledger"], row["slot"]
    schedule, backend = _schedule(row)
    expected_contract = {
        "policy": schedule["policy"],
        "retry": schedule["retry"],
        "cache": "reuse",
        "artifact_domain": list(digests.values()),
    }
    if document_digest(ledger["contract"]) != document_digest(expected_contract):
        raise ValueError("application contract differs from frozen policy")
    scope = Scope(**ledger["scope"])
    if scope.attempt != int(bool(slot["planned_auxiliary_entries"])):
        raise ValueError("application attempt differs from frozen lifecycle")
    if row["status"] != "completed" and "observations" not in row:
        if "path_frame" in row:
            raise ValueError("path frame retained without its observer snapshots")
        return
    _validate_observations(row, digests)
    _validate_frame(row, digests, backend)


def _reference_binding(ledger: dict[str, Any], digests: dict[str, str]) -> tuple[str, bool]:
    scope, contract = ledger["scope"], ledger["contract"]
    root, chosen = ledger["root_binding"], ledger["selection"]
    for binding_scope in (scope, root["scope"], chosen["scope"]):
        Scope(**binding_scope)
    if root["scope"] != {"request": scope["request"], "attempt": 0}:
        raise ValueError("reference root scope mismatch")
    if chosen["scope"] != scope or root["phase"] != contract["policy"]:
        raise ValueError("reference application binding mismatch")
    inherited = scope["attempt"] > 0 and contract["retry"] == "inherit"
    pinned = scope["attempt"] == 0 and contract["policy"] == "pin_at_acceptance"
    expected = root["digest"] if inherited or pinned else chosen["digest"]
    if expected not in digests.values() or chosen["digest"] not in digests.values():
        raise ValueError("reference selected artifact outside sealed domain")
    if inherited and (
        chosen["parent_scope"] != root["scope"] or chosen["parent_selection"] != root["selection"]
    ):
        raise ValueError("reference retry binding mismatch")
    phase = contract["retry"] if scope["attempt"] else contract["policy"]
    rule_violation = chosen["phase"] != phase or (inherited and chosen["digest"] != root["digest"])
    return str(expected), bool(rule_violation)


def _entry_snapshot(entry: dict[str, Any], scope: dict[str, Any], digests: dict[str, str]) -> str:
    Scope(**entry["scope"])
    digest = content_sha256(bytes.fromhex(entry["raw_hex"]))
    if digest != entry["sha256"] or digest not in digests.values() or entry["scope"] != scope:
        raise ValueError("native entry snapshot mismatch")
    if type(entry["completed"]) is not bool:
        raise ValueError("native completion must be a boolean")
    return digest


def _reference_entries(
    row: dict[str, Any], key: str, scope: dict[str, Any], digests: dict[str, str]
) -> list[str]:
    ledger, values = row["ledger"], row["ledger"][key]
    if len({entry["occurrence"] for entry in values}) != len(values):
        raise ValueError("reference duplicates an actual native occurrence")
    offered = []
    for entry in values:
        offered.append(_entry_snapshot(entry, scope, digests))
        if row["status"] == "completed" and not entry["completed"]:
            raise ValueError("completed slot contains failed native entry")
    completion_key = (
        "sdk_completed_loads" if key == "target_entries" else "sdk_completed_auxiliary_loads"
    )
    completed = ledger[completion_key]
    if type(completed) is not int or not 0 <= completed <= sum(e["completed"] for e in values):
        raise ValueError("SDK completion count contradicts native completions")
    if row["status"] == "completed" and completed != len(values):
        raise ValueError("completed slot lacks SDK load completion")
    return offered


def _false_blocks(
    ledger: dict[str, Any], digests: dict[str, str], expected: str, rule_violation: bool
) -> int:
    false_blocks = 0
    for blocked in ledger["blocked"]:
        if blocked["scope"] != ledger["scope"] or type(blocked["prior_entries"]) is not int:
            raise ValueError("blocked proposal binding mismatch")
        prior = blocked["prior_entries"]
        if not 0 <= prior <= len(ledger["target_entries"]):
            raise ValueError("blocked proposal references an impossible occurrence count")
        digest = content_sha256(bytes.fromhex(blocked["raw_hex"]))
        if digest not in digests.values():
            raise ValueError("blocked proposal outside sealed domain")
        false_blocks += int(prior == 0 and digest == expected and not rule_violation)
    return false_blocks


def reference(row: dict[str, Any], digests: dict[str, str]) -> dict[str, Any]:
    """Join actual application binding and entry snapshots, not candidate decisions."""
    if "ledger" not in row:
        if row["status"] == "completed":
            raise ValueError("completed slot lacks its reference ledger")
        return {"verdict": "unknown", "assessable": False, "eligibility": "unavailable"}
    ledger = row["ledger"]
    if type(ledger["closed"]) is not bool:
        raise ValueError("reference closure must be a boolean")
    if row["status"] == "completed" and not ledger["closed"]:
        raise ValueError("completed attempt lacks authoritative reference closure")
    expected, rule_violation = _reference_binding(ledger, digests)
    offered = _reference_entries(row, "target_entries", ledger["scope"], digests)
    _reference_entries(row, "auxiliary_entries", ledger["root_binding"]["scope"], digests)
    false_blocks = _false_blocks(ledger, digests, expected, rule_violation)
    if row["status"] != "completed":
        return {
            "verdict": "unknown",
            "assessable": False,
            "eligibility": "unavailable",
            "required_digest": expected,
            "offered": offered,
            "false_blocks": false_blocks,
        }
    violation = (
        len(offered) > 1
        or any(digest != expected for digest in offered)
        or bool(offered)
        and rule_violation
    )
    return {
        "verdict": "violation" if violation else "compliant" if offered else None,
        "assessable": True,
        "eligibility": "load" if offered else "no_new_load",
        "required_digest": expected,
        "offered": offered,
        "false_blocks": false_blocks,
    }


def decisions(row: dict[str, Any], directory: Path) -> dict[str, Any]:
    if row["status"] != "completed":
        return {
            cutoff: {
                method: {
                    "verdict": "unknown",
                    "reason": "unavailable_execution",
                    "eligibility": "undetermined",
                }
                for method in METHODS
            }
            for cutoff in CUTOFFS
        }
    outputs = {}
    for cutoff in CUTOFFS:
        admitted = observation(row["observations"][cutoff])
        p = provenance_checker(admitted, directory / cutoff)
        outputs[cutoff] = {
            "S": asdict(receipt_checker(admitted)),
            "T": asdict(completion_monitor(admitted)),
            "P": {
                **asdict(p.decision),
                "verifier_calls": p.verifier_calls,
                "artifact_rules_passed": p.artifact_rules_passed,
            },
        }
    return outputs


def annotate(row: dict[str, Any], digests: dict[str, str], directory: Path) -> dict[str, Any]:
    value = {key: item for key, item in row.items() if key != "row_sha256"}
    validate_evidence(value, digests)
    value["reference"] = reference(value, digests)
    value["decisions"] = decisions(value, directory)
    value["row_sha256"] = document_digest(value)
    return value


def canonical_frame(value: dict[str, Any], request: str) -> dict[str, Any]:
    """Only consistent request/root/URI nonce renaming; never drop model selectors."""
    copied: dict[str, Any] = json.loads(json.dumps(value))
    for key in ("public_request_id", "public_root_id"):
        if copied[key] != request:
            raise ValueError("path collector envelope identity mismatch")
        copied[key] = "request-0"
    prefix = "spool://" + request + "/"
    if not copied["requested_uri"].startswith(prefix):
        raise ValueError("path collector URI identity mismatch")
    copied["requested_uri"] = "spool://request-0/" + copied["requested_uri"][len(prefix) :]
    return copied


def canonical_scoped(value: dict[str, Any]) -> dict[str, Any]:
    """Normalize the request nonce in scope/token references, retaining every fact."""
    copied: dict[str, Any] = json.loads(json.dumps(value))
    request = copied["scope"]["request"]
    copied["scope"]["request"] = "request-0"
    for record in copied["records"]:
        for key in ("scope", "parent_scope"):
            scope = record[key]
            if scope is not None:
                if scope["request"] != request:
                    raise ValueError("scoped evidence contains another request identity")
                scope["request"] = "request-0"
        for key in ("selection", "parent_selection"):
            token = record[key]
            if token is not None:
                if not token.startswith(request + ":"):
                    raise ValueError("scoped token lacks its request binding")
                record[key] = "request-0:" + token[len(request) + 1 :]
    return copied


def equality_counts(rows: list[dict[str, Any]], cutoff: str | None) -> dict[str, int]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["status"] == "completed":
            frame = (
                canonical_scoped(row["observations"][cutoff])
                if cutoff is not None
                else canonical_frame(row["path_frame"], row["ledger"]["scope"]["request"])
            )
            groups[document_digest(frame)].append(row)
    opposite = [
        group
        for group in groups.values()
        if {row["reference"]["verdict"] for row in group} == {"compliant", "violation"}
    ]
    return {
        "exact_evidence_groups": len(groups),
        "opposite_status_groups": len(opposite),
        "opposite_status_slots": sum(map(len, opposite)),
    }


def _pair_counts(rows: list[dict[str, Any]], method: str, cutoff: str) -> dict[str, Any]:
    choices = [(row["decisions"][cutoff][method], row["reference"]) for row in rows]
    assessable = [(choice, truth) for choice, truth in choices if truth["assessable"]]
    correct = sum(
        c["verdict"] in {"compliant", "violation"} and c["verdict"] == t["verdict"]
        for c, t in assessable
    )
    return {
        "planned_denominator": len(rows),
        "correct_identified": correct,
        "warranted_coverage": correct / len(rows) if rows else None,
        "reference_assessable_denominator": len(assessable),
        "reference_unavailable": len(rows) - len(assessable),
        "false_compliance": sum(
            c["verdict"] == "compliant" and t["verdict"] != "compliant" for c, t in assessable
        ),
        "false_violation": sum(
            c["verdict"] == "violation" and t["verdict"] != "violation" for c, t in assessable
        ),
        "verdict_counts": dict(Counter(c["verdict"] or "no_new_load" for c, _ in choices)),
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    eligible = [
        r
        for r in rows
        if r["slot"]["branch"] == "observation" and r["slot"]["planned_target_entries"]
    ]
    if len(rows) != 48 or len(eligible) != 22:
        raise ValueError("fixed validation denominator changed")
    comparison = {
        cutoff: {method: _pair_counts(eligible, method, cutoff) for method in METHODS}
        for cutoff in CUTOFFS
    }
    per_group = {}
    for backend in ("onnxruntime", "skops"):
        for family in ("handoff", "retry_delivery", "native_reentry", "warm_cache"):
            group = [
                r
                for r in eligible
                if r["slot"]["backend"] == backend and r["slot"]["family"] == family
            ]
            per_group[backend + ":" + family] = {
                cutoff: {method: _pair_counts(group, method, cutoff) for method in METHODS}
                for cutoff in CUTOFFS
            }
    path_equality = equality_counts(eligible, None)
    prevention = [r for r in rows if r["slot"]["branch"] == "prevention"]
    successful = [r for r in prevention if r["status"] == "completed"]
    native = [
        entry
        for r in rows
        for key in ("target_entries", "auxiliary_entries")
        for entry in r.get("ledger", {}).get(key, [])
    ]
    false = sum(
        pair["false_compliance"] + pair["false_violation"]
        for outputs in comparison.values()
        for pair in outputs.values()
    )
    complete = all(r["status"] == "completed" for r in rows)
    return {
        "planned_slots": 48,
        "planned_load_slots": 44,
        "planned_cache_slots": 4,
        "loader_format_strata": 2,
        "independent_deployments": 0,
        "status_counts": dict(Counter(r["status"] for r in rows)),
        "retained_native_entries": len(native),
        "completed_native_entries": sum(entry["completed"] for entry in native),
        "completed_sdk_loads": sum(r.get("ledger", {}).get("sdk_completed_loads", 0) for r in rows),
        "completed_auxiliary_sdk_loads": sum(
            r.get("ledger", {}).get("sdk_completed_auxiliary_loads", 0) for r in rows
        ),
        "slots_with_unavailable_native_census": sum(
            r["status"]
            in {
                "slot_timeout",
                "worker_failure",
                "invalid_worker_output",
                "reference_integrity_failure",
            }
            for r in rows
        ),
        "comparisons": comparison,
        "per_backend_family": per_group,
        "exact_path_frame_groups": path_equality["exact_evidence_groups"],
        "opposite_status_groups": path_equality["opposite_status_groups"],
        "opposite_status_slots": path_equality["opposite_status_slots"],
        "scoped_evidence_equality": {
            cutoff: equality_counts(eligible, cutoff) for cutoff in CUTOFFS
        },
        "prevention": {
            "planned_opportunities": 24,
            "unavailable": 24 - len(successful),
            "blocked_proposals": sum(len(r["ledger"]["blocked"]) for r in successful),
            "false_blocks": sum(r["reference"]["false_blocks"] for r in successful),
            "compliant_load_completions": sum(
                r["reference"]["verdict"] == "compliant" and r["ledger"]["sdk_completed_loads"] > 0
                for r in successful
            ),
            "residual_violations": sum(
                r["reference"]["verdict"] == "violation" for r in successful
            ),
            "cache_only": sum(not r["slot"]["planned_target_entries"] for r in successful),
            "blocked_without_load": sum(
                bool(r["ledger"]["blocked"]) and not r["ledger"]["target_entries"]
                for r in successful
            ),
        },
        "disposition": "incomplete_validation"
        if not complete
        else "baseline_or_capture_contract_gap"
        if false
        else "bounded_transfer_no_new_checker_advantage",
        "provider_calls": 0,
        "limitations": "two loader strata sharing one authored scheduler; honest local capture; no population inference, host or serving attestation",
    }


def verify_rows(
    rows: list[dict[str, Any]], slots: list[dict[str, Any]], digests: dict[str, dict[str, str]]
) -> dict[str, Any]:
    if document_digest([row["slot"] for row in rows]) != document_digest(slots):
        raise ValueError("validation slot census changed")
    with tempfile.TemporaryDirectory(prefix="model-load-replay-") as temp:
        for index, row in enumerate(rows):
            unsigned = {k: v for k, v in row.items() if k != "row_sha256"}
            if document_digest(unsigned) != row.get("row_sha256"):
                raise ValueError("validation row hash changed")
            validate_evidence(row, digests[row["slot"]["backend"]])
            if document_digest(reference(row, digests[row["slot"]["backend"]])) != document_digest(
                row["reference"]
            ):
                raise ValueError("reference changed during hash-only replay")
            if document_digest(decisions(row, Path(temp) / str(index))) != document_digest(
                row["decisions"]
            ):
                raise ValueError("candidate decision changed during replay")
            if "ledger" in row:
                ledger, slot = row["ledger"], row["slot"]
                if (
                    len(ledger["target_entries"]) > slot["planned_target_entries"]
                    or len(ledger["auxiliary_entries"]) > slot["planned_auxiliary_entries"]
                ):
                    raise ValueError("recorded native budget exceeded")
                if slot["branch"] == "observation" and ledger["blocked"]:
                    raise ValueError("observation branch unexpectedly intervened")
                if slot["branch"] == "prevention" and len(ledger["target_entries"]) > 1:
                    raise ValueError("prevention token admitted native reentry")
    return summarize(rows)
