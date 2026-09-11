"""Prospective measurement calibration: source capacity, identity and part coverage.

The historical V2 cohort is a development observation, not V3 study data. Target
paths are assigned before generation; expected values are evaluator-only. A frame
intent is never used as an automatic or human label.
"""

from __future__ import annotations

import itertools
import json
import re
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any

from aletheia_lab.evaluation.claim_corpus_normalization_recovery import (
    ProviderDiagnosisOutputV2,
    provider_response_schema_v2,
)
from aletheia_lab.evaluation.claim_corpus_readiness import FAIRNESS_PATH
from aletheia_lab.evaluation.claim_evidence_semantics import (
    ModelVisibleEvidenceContext,
    build_visible_evidence_item,
)
from aletheia_lab.evaluation.claim_validation_v2_relation_frames import build_visible_context
from aletheia_lab.evaluation.claim_validation_v2_runtime import (
    _load_inputs,
    build_v2_runtime_manifest,
)
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256 as digest
from aletheia_lab.evaluation.variant_fairness import load_diagnosis_variant_freeze
from aletheia_lab.project.identity import content_sha256

V3_VERSION = "claim-support-validation-v3/1"
VERSION = "claim-support-validation-v3/2"
VARIANTS = ("A1", "A2", "A3", "B1", "B2", "CodeGraph", "FULL")
CONDITIONS = ("full", "missing_key", "noisy")
FRAMES = ("natural", "withdrawal", "partial", "counter")
LABELS = ("fully_supported", "unsupported", "partially_supported", "contradicted")
PREDECESSOR_CLOSEOUT = "024ad9192dc0ae6316cac7fe558fa807f37c0e221d80f85b869715f8927218eb"
PREDECESSOR_CENSUS = "1bdc592468e2bd64832d777955abbc18e62f29183d8034a5c00b7576f934a952"
PREFIX = "In the displayed measurement report, "


def numeric_leaves(content: str) -> dict[str, str]:
    """Exact RFC 6901 pointers, including categorical keys; no IDs or booleans."""
    payload = json.loads(content, parse_float=Decimal, parse_int=Decimal)
    result: dict[str, str] = {}

    def visit(value: object, pointer: str) -> None:
        if isinstance(value, dict):
            for key, child in sorted(value.items()):
                escaped = str(key).replace("~", "~0").replace("/", "~1")
                visit(child, f"{pointer}/{escaped}")
        elif isinstance(value, Decimal) and value.is_finite():
            result[pointer] = format(value, "f")

    visit(payload, "")
    return result


def facts(context: ModelVisibleEvidenceContext) -> dict[tuple[str, str], str]:
    return {
        (item.evidence_id, path): value
        for item in context.items
        if item.evidence_id != "ev-source-provenance"
        for path, value in numeric_leaves(item.content).items()
        if path.startswith("/payload/")
    }


def witness(parts: list[tuple[str, str, str]]) -> dict[str, Any]:
    texts = [f"{PREFIX}{eid}#{path} = {value}" for eid, path, value in parts]
    return {
        "claim_type": "evidence_statement",
        "claim_text": "; ".join(texts),
        "material_parts": [{"text": text} for text in texts],
        "visible_evidence_ids": sorted({part[0] for part in parts}),
    }


def source_bank(context: ModelVisibleEvidenceContext) -> list[dict[str, Any]]:
    """All prespecified key-signal/performance pairs, not paraphrases or new facts."""
    data = facts(context)
    performance = sorted(
        key
        for key in data
        if key[0] == "ev-performance-summary"
        and key[1].startswith(("/payload/observed/", "/payload/delta/"))
    )
    key_signals = sorted(
        key
        for key in data
        if key[0] == "ev-key-measurement"
        and not key[1].endswith(("row_count", "column_count", "positive_count"))
        and "/reference" not in key[1]
    )
    pairs = itertools.product(key_signals, performance)
    if not key_signals:
        # Missing-key contexts contribute natural claims only; never invent key facts.
        return [witness([(eid, path, data[eid, path])]) for eid, path in performance]
    return [witness([(a[0], a[1], data[a]), (b[0], b[1], data[b])]) for a, b in pairs]


SOURCE_SCHEMA_VERSION = "claim-source-measurement-output/1"


def _source_targets(expected: list[dict[str, Any]]) -> list[tuple[int, str, str, str]]:
    """Flatten frozen claim parts into provider-visible target ordinals."""
    return [
        (target, evidence_id, pointer, value)
        for target, (evidence_id, pointer, value) in enumerate(
            (part for claim in expected for part in target_parts(claim)), start=1
        )
    ]


def source_response_schema(expected: list[dict[str, Any]]) -> dict[str, Any]:
    """Constrain transport structure without disclosing evaluator-side values."""
    targets = _source_targets(expected)
    row = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "target": {"type": "integer", "enum": [target for target, *_ in targets]},
            "value": {"type": "string"},
        },
        "required": ["target", "value"],
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "schema_version": {"type": "string", "const": SOURCE_SCHEMA_VERSION},
            "readings": {
                "type": "array",
                "items": row,
                "minItems": len(targets),
                "maxItems": len(targets),
            },
        },
        "required": ["schema_version", "readings"],
    }


def source_payload(expected: list[dict[str, Any]]) -> dict[str, Any]:
    """Return the exact provider payload expected by an offline rehearsal."""
    return {
        "schema_version": SOURCE_SCHEMA_VERSION,
        "readings": [
            {"target": target, "value": value} for target, _, _, value in _source_targets(expected)
        ],
    }


def legacy_source_payload(expected: list[dict[str, Any]]) -> dict[str, Any]:
    """Rebuild the retired V3 envelope for read-only failure audit."""
    return {
        "schema_version": "diagnosis-provider-output/2",
        "result": {"output_status": "completed", "atomic_claims": expected},
    }


def render_source_payload(
    payload: dict[str, Any], expected: list[dict[str, Any]]
) -> dict[str, Any]:
    """Validate readings and deterministically render the frozen claim structure."""
    issue = source_payload_issue(payload, expected)
    if issue is not None:
        raise ValueError(issue)
    readings = payload["readings"]
    values = [row["value"] for row in readings]
    cursor = iter(values)
    claims = []
    for claim in expected:
        parts = [(eid, pointer, next(cursor)) for eid, pointer, _ in target_parts(claim)]
        claims.append(witness(parts))
    rendered = {
        "schema_version": "diagnosis-provider-output/2",
        "result": {"output_status": "completed", "atomic_claims": claims},
    }
    ProviderDiagnosisOutputV2.model_validate_json(json.dumps(rendered))
    return rendered


def source_payload_issue(payload: dict[str, Any], expected: list[dict[str, Any]]) -> str | None:
    """Return one stable, non-content-bearing semantic issue code."""
    if not isinstance(payload, dict) or set(payload) != {"schema_version", "readings"}:
        return "source_envelope_invalid"
    if payload.get("schema_version") != SOURCE_SCHEMA_VERSION:
        return "source_schema_version_mismatch"
    readings = payload.get("readings")
    targets = _source_targets(expected)
    if not isinstance(readings, list) or len(readings) != len(targets):
        return "source_reading_census_mismatch"
    for row, (target, _, _, expected_value) in zip(readings, targets, strict=True):
        if not isinstance(row, dict) or set(row) != {"target", "value"}:
            return "source_reading_shape_invalid"
        if type(row["target"]) is not int or row["target"] != target:
            return "source_target_order_mismatch"
        if not isinstance(row["value"], str) or row["value"] != expected_value:
            return "source_value_mismatch"
    return None


def accept_source(payload: dict[str, Any], expected: list[dict[str, Any]]) -> bool:
    """Accept exact readings only; deterministic rendering never repairs a value."""
    return source_payload_issue(payload, expected) is None


def source_instance_id(protocol: str, scheduled_request: str, output: str, ordinal: int) -> str:
    if (
        type(ordinal) is not int
        or ordinal < 1
        or any(
            re.fullmatch(r"[0-9a-f]{64}", value) is None
            for value in (protocol, scheduled_request, output)
        )
    ):
        raise ValueError("invalid source instance binding")
    return digest(
        {
            "version": VERSION,
            "protocol": protocol,
            "request": scheduled_request,
            "output_content": output,
            "claim_ordinal": ordinal,
        }
    )


def relation_instance_id(source_instance: str, frame: str, context: str) -> str:
    if frame not in FRAMES or any(
        re.fullmatch(r"[0-9a-f]{64}", value) is None for value in (source_instance, context)
    ):
        raise ValueError("unknown V3 frame")
    return digest(
        {
            "version": VERSION,
            "source_instance": source_instance,
            "frame": frame,
            "visible_context": context,
        }
    )


def relation_schema(parts: int, evidence_ids: list[str]) -> dict[str, Any]:
    row = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "part": {"type": "integer", "enum": list(range(1, parts + 1))},
            "evidence_id": {"type": "string", "enum": evidence_ids},
            "relation": {"type": "string", "enum": ["supports", "contradicts", "neutral"]},
        },
        "required": ["part", "evidence_id", "relation"],
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "relations": {
                "type": "array",
                "items": row,
                "minItems": parts * len(evidence_ids),
                "maxItems": parts * len(evidence_ids),
            }
        },
        "required": ["relations"],
    }


def reduce_relations(payload: dict[str, Any], parts: int, evidence_ids: list[str]) -> str:
    """Union per-part support, contradiction first; reject missing/duplicate cells."""
    if (
        type(parts) is not int
        or parts < 1
        or not evidence_ids
        or any(not isinstance(eid, str) for eid in evidence_ids)
        or len(set(evidence_ids)) != len(evidence_ids)
    ):
        raise ValueError("invalid relation matrix dimensions")
    if set(payload) != {"relations"} or not isinstance(payload["relations"], list):
        raise ValueError("invalid part-relation envelope")
    expected = {(p, eid) for p in range(1, parts + 1) for eid in evidence_ids}
    rows = payload["relations"]
    seen: set[tuple[int, str]] = set()
    supported: set[int] = set()
    contradicted = False
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"part", "evidence_id", "relation"}:
            raise ValueError("invalid part-relation cell")
        if (
            type(row["part"]) is not int
            or not isinstance(row["evidence_id"], str)
            or not isinstance(row["relation"], str)
        ):
            raise ValueError("invalid part-relation identity")
        key = row["part"], row["evidence_id"]
        if (
            key not in expected
            or key in seen
            or row["relation"] not in {"supports", "contradicts", "neutral"}
        ):
            raise ValueError("duplicate, missing or foreign part-relation cell")
        seen.add(key)
        if row["relation"] == "supports":
            supported.add(row["part"])
        contradicted |= row["relation"] == "contradicts"
    if seen != expected or not expected:
        raise ValueError("incomplete part-relation matrix")
    return (
        "contradicted"
        if contradicted
        else (
            "fully_supported"
            if len(supported) == parts
            else "partially_supported"
            if supported
            else "unsupported"
        )
    )


def target_parts(claim: dict[str, Any]) -> list[tuple[str, str, str]]:
    result = []
    for part in claim["material_parts"]:
        text = part["text"]
        if not text.startswith(PREFIX):
            raise ValueError("claim lacks the displayed-report scope")
        location, value = text[len(PREFIX) :].rsplit(" = ", 1)
        eid, path = location.split("#", 1)
        result.append((eid, path, value))
    if claim != witness(result):
        raise ValueError("noncanonical measurement claim")
    return result


def structural_relations(
    claim: dict[str, Any], context: ModelVisibleEvidenceContext
) -> dict[str, Any]:
    """Evaluator-only numeric oracle for frame eligibility and synthetic qualification."""
    data = facts(context)
    rows = []
    for index, (source_id, path, expected) in enumerate(target_parts(claim), 1):
        for item in context.items:
            actual = data.get((item.evidence_id, path)) if item.evidence_id == source_id else None
            polarity = (
                "neutral"
                if actual is None
                else ("supports" if Decimal(actual) == Decimal(expected) else "contradicts")
            )
            rows.append({"part": index, "evidence_id": item.evidence_id, "relation": polarity})
    return {"relations": rows}


def frame_contexts(
    source: ModelVisibleEvidenceContext,
    counter: ModelVisibleEvidenceContext,
) -> dict[str, ModelVisibleEvidenceContext]:
    """Copy authenticated whole items; removal is not fabrication or partial JSON repair."""
    by_id = {item.evidence_id: item for item in source.items}
    result = {
        "natural": source,
        "counter": counter,
        "withdrawal": build_visible_context((by_id["ev-source-provenance"],)),
    }
    if "ev-key-measurement" in by_id:
        result["partial"] = build_visible_context((by_id["ev-key-measurement"],))
    return result


def build_design(root: Path) -> dict[str, Any]:
    """Freeze target slots from authentic inputs before any V3 provider output."""
    _, census, evidence = _load_inputs(root)
    if evidence.census_sha256 != PREDECESSOR_CENSUS:
        raise ValueError("unexpected development evidence census")
    manifest = build_v2_runtime_manifest(root)
    by_pair = {(b.family_id, b.evidence_condition): b for b in evidence.bindings}
    by_sha = {b.visible_context.context_sha256: b.visible_context for b in evidence.bindings}
    counters = {
        (f.family_id, f.evidence_condition): by_sha[f.direct_counterevidence_context_sha256]
        for f in manifest.source_frames
    }
    cursors: Counter[str] = Counter()
    slots: list[dict[str, Any]] = []
    for schedule in manifest.diagnosis_schedule:
        binding = by_pair[schedule.family_id, schedule.evidence_condition]
        context = binding.visible_context
        bank = source_bank(context)
        if len(bank) < 2:
            raise ValueError("source bank cannot fill two slots")
        # Full/noisy share an allocation cursor: no automatic reuse of the same witness.
        bucket = schedule.family_id + (
            "single" if schedule.evidence_condition == "missing_key" else "pair"
        )
        start = cursors[bucket]
        selected = [bank[(start + index) % len(bank)] for index in range(2)]
        cursors[bucket] += 2
        eligible = []
        frames = frame_contexts(context, counters[schedule.family_id, schedule.evidence_condition])
        for claim in selected:
            labels = {
                frame: reduce_relations(
                    structural_relations(claim, ctx),
                    len(claim["material_parts"]),
                    [i.evidence_id for i in ctx.items],
                )
                for frame, ctx in frames.items()
            }
            eligible.append(
                [
                    frame
                    for frame, label in zip(FRAMES, LABELS, strict=True)
                    if labels.get(frame) == label
                ]
            )
        payload: dict[str, Any] = {
            "source_schedule": schedule.model_dump(mode="json"),
            "source_binding_sha256": binding.binding_sha256,
            "context": context.model_dump(mode="json"),
            "expected": selected,
            "eligible_frames": eligible,
            "frames": {name: ctx.model_dump(mode="json") for name, ctx in frames.items()},
        }
        slots.append({**payload, "slot_sha256": digest({"version": VERSION, **payload})})
    capacities = capacity_assignment(slots)
    relation_census = [
        {"frame": frame, **row} for frame, rows in capacities["assignments"].items() for row in rows
    ]
    result = {
        "schema_version": VERSION,
        "predecessor_closeout_sha256": PREDECESSOR_CLOSEOUT,
        "evidence_census_sha256": evidence.census_sha256,
        "source_request_census_sha256": census.census_sha256,
        "source_slot_count": len(slots),
        "source_claim_instance_count": 2 * len(slots),
        "distinct_target_text_count": len({c["claim_text"] for s in slots for c in s["expected"]}),
        "capacity_assignment": capacities,
        "relation_census": relation_census,
        "relation_request_count": len(relation_census),
        "unassigned_frames_must_not_execute": True,
        "slots": slots,
        "estimand": "human_agreement_with_part_coverage_on_authentic_measurement_challenges",
        "variant_superiority_claims_permitted": False,
        "free_form_diagnosis_generalization_permitted": False,
        "observed_v3_labels": False,
        "cohort_execution_authorized": False,
    }
    return {**result, "design_sha256": digest(result)}


def build_relation_tasks(
    protocol_sha: str, design: dict[str, Any], slot: dict[str, Any], output: dict[str, Any]
) -> list[dict[str, Any]]:
    """Project the verified frozen design; this constructor grants no live authority."""
    if design.get("design_sha256") != digest(
        {k: v for k, v in design.items() if k != "design_sha256"}
    ):
        raise ValueError("design binding mismatch")
    if slot not in design["slots"]:
        raise ValueError("source slot binding differs from design")
    identity = {k: v for k, v in slot.items() if k != "slot_sha256"}
    if slot.get("slot_sha256") != digest({"version": VERSION, **identity}):
        raise ValueError("source slot binding mismatch")
    try:
        rendered = render_source_payload(output, slot["expected"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("provider output differs from frozen source targets") from exc
    result = []
    for ordinal, claim in enumerate(rendered["result"]["atomic_claims"], 1):
        instance = source_instance_id(protocol_sha, slot["slot_sha256"], digest(output), ordinal)
        frames = [
            row["frame"]
            for row in design["relation_census"]
            if row["slot"] == slot["slot_sha256"] and row["ordinal"] == ordinal
        ]
        for frame in frames:
            context = ModelVisibleEvidenceContext.model_validate_json(
                json.dumps(slot["frames"][frame])
            )
            result.append(
                {
                    "source_instance_sha256": instance,
                    "relation_instance_sha256": relation_instance_id(
                        instance, frame, context.context_sha256
                    ),
                    "frame": frame,
                    "provider_payload": {
                        "claim": claim,
                        "visible_evidence": context.model_payload(),
                    },
                }
            )
    return result


def capacity_assignment(slots: list[dict[str, Any]]) -> dict[str, Any]:
    """Construct a conservative disjoint-text certificate with existing quota caps.

    This is an intended-frame capacity certificate, NOT a sample or predicted labels.
    Greedy failure is a safe blocker, not proof of mathematical infeasibility.
    """
    used: set[str] = set()
    assignment: dict[str, list[dict[str, Any]]] = {}
    for frame in ("counter", "withdrawal", "partial", "natural"):
        families: Counter[str] = Counter()
        outputs: Counter[str] = Counter()
        rows = []
        for slot in sorted(slots, key=lambda s: s["slot_sha256"]):
            family = slot["source_schedule"]["family_id"]
            for ordinal, claim in enumerate(slot["expected"]):
                text = claim["claim_text"]
                if (
                    frame not in slot["eligible_frames"][ordinal]
                    or text in used
                    or families[family] >= 5
                    or outputs[slot["slot_sha256"]] >= 2
                ):
                    continue
                rows.append(
                    {
                        "slot": slot["slot_sha256"],
                        "ordinal": ordinal + 1,
                        "text_sha256": digest(text),
                        "family_id": family,
                    }
                )
                used.add(text)
                families[family] += 1
                outputs[slot["slot_sha256"]] += 1
                if len(rows) == 60:
                    break
            if len(rows) == 60:
                break
        assignment[frame] = rows
    counts = {frame: len(rows) for frame, rows in assignment.items()}
    return {
        "counts": counts,
        "minimum_per_frame": 60,
        "assignments": assignment,
        "passed": all(value >= 60 for value in counts.values()),
        "labels_are_structural_capacity_only": True,
    }


def make_synthetic_context(index: int, condition: str) -> ModelVisibleEvidenceContext:
    """Disjoint calibration inputs; values deliberately include zero and negatives."""
    contents: dict[str, dict[str, Any]] = {
        "ev-performance-summary": {
            "observed": {"accuracy": 0.731 + index / 1000, "macro_f1": 0.62 + index / 1000},
            "delta": {"accuracy": -0.013 - index / 1000, "macro_f1": 0},
        },
        "ev-source-provenance": {"origin": "synthetic qualification; excluded from study"},
    }
    if condition != "missing_key":
        contents["ev-key-measurement"] = {
            "changed_cell_count": 14 + index,
            "observed_shares": {"category with space": 0.35 + index / 1000},
        }
    if condition == "noisy":
        contents["ev-log"] = {"irrelevant_count": 7}
    return build_visible_context(
        tuple(
            build_visible_evidence_item(
                evidence_id=eid,
                kind="metric" if eid != "ev-source-provenance" else "artifact",
                title="Measurement report" if eid != "ev-source-provenance" else "Source record",
                content=json.dumps({"payload": body}, sort_keys=True, separators=(",", ":")),
                source_content_sha256=digest({"synthetic": index, "item": eid, "body": body}),
            )
            for eid, body in contents.items()
        )
    )


def source_prompt(expected: list[dict[str, Any]], *, synthetic: bool = True) -> str:
    # Publish paths and formatting, never expected numbers or frame intents.
    targets = [
        {"target": target, "evidence_id": eid, "json_pointer": path}
        for target, eid, path, _ in _source_targets(expected)
    ]
    return (
        (
            "Synthetic transport calibration, excluded from study data. "
            if synthetic
            else "Prospectively registered measurement calibration. "
        )
        + "For this calibration task, use the following output contract instead of free-form diagnosis. "
        "Follow the source-measurement schema. Return one reading for every target in the listed order. "
        "Copy each target's exact base-10 numeric value from the visible evidence; do not calculate, "
        "round, reformat or add units. Use the listed target integer unchanged. Claim text, material-part "
        "scope and citations are rendered deterministically after exact local validation. These are "
        "measurement checks, not causal diagnosis. "
        "Targets: " + json.dumps(targets, sort_keys=True, separators=(",", ":"))
    )


def _legacy_source_prompt(expected: list[dict[str, Any]]) -> str:
    targets = [
        [{"evidence_id": eid, "json_pointer": path} for eid, path, _ in target_parts(c)]
        for c in expected
    ]
    return (
        "Synthetic transport calibration, excluded from study data. "
        "For this calibration task, use the following output contract instead of free-form diagnosis. "
        "Follow the source-output schema. Return exactly two evidence_statement claims in target order. "
        "For each target copy its exact base-10 numeric value from the visible evidence; do not "
        "calculate or round. Each material part is 'In the displayed measurement report, "
        "<evidence_id>#<json_pointer> = <value>'. Join material parts with '; ' to make claim_text. "
        "Cite sorted unique target evidence IDs. Return completed only if every target is visible. "
        "These are measurement checks, not causal diagnosis. Targets: "
        + json.dumps(targets, sort_keys=True, separators=(",", ":"))
    )


RELATION_PROMPT = (
    "Evaluate each material part of the supplied claim against each supplied evidence item. "
    "The claim is explicitly about the displayed measurement report, not an absent original experiment. "
    "Return one cell for every (part number, evidence ID) pair. Use supports for an exact established "
    "assertion, contradicts for an explicitly different value at the asserted report field, and neutral "
    "when that field or report item is absent. Absence is not contradiction. Ignore unrelated numbers. "
    "Use only the supplied evidence; do not infer hidden conditions or labels."
)


def _build_probes(root: Path | None, *, legacy: bool) -> list[dict[str, Any]]:
    freeze = load_diagnosis_variant_freeze(root / FAIRNESS_PATH) if root is not None else None
    probes = []
    for index, (condition, variant) in enumerate(itertools.product(CONDITIONS, VARIANTS)):
        context = make_synthetic_context(index, condition)
        expected = source_bank(context)[:2]
        probes.append(
            {
                "kind": "source",
                "variant": variant,
                "condition": condition,
                "context": context.model_dump(mode="json"),
                "expected": expected,
                "prompt": (
                    (freeze.prompt_policies[variant].instruction_contract + "\n\n")
                    if freeze
                    else ""
                )
                + (_legacy_source_prompt(expected) if legacy else source_prompt(expected)),
                "schema": (
                    provider_response_schema_v2(
                        visible_evidence_ids=tuple(i.evidence_id for i in context.items)
                    )
                    if legacy
                    else source_response_schema(expected)
                ),
            }
        )
    for index in range(3):
        context = make_synthetic_context(index + 30, "full")
        counter = make_synthetic_context(index + 40, "full")
        claim = source_bank(context)[0]
        for frame, ctx in frame_contexts(context, counter).items():
            probes.append(
                {
                    "kind": "relation",
                    "variant": "FULL",
                    "condition": frame,
                    "context": ctx.model_dump(mode="json"),
                    "claim": claim,
                    "expected": structural_relations(claim, ctx),
                    "prompt": RELATION_PROMPT,
                    "schema": relation_schema(2, [i.evidence_id for i in ctx.items]),
                }
            )
    version = V3_VERSION if legacy else VERSION
    return [{**p, "probe_sha256": digest({"version": version, **p})} for p in probes]


def build_probes(root: Path | None = None) -> list[dict[str, Any]]:
    return _build_probes(root, legacy=False)


def build_v3_probes(root: Path | None = None) -> list[dict[str, Any]]:
    """Rebuild only the retired synthetic census; never use it for a new call."""
    return _build_probes(root, legacy=True)


def implementation_bindings(root: Path) -> dict[str, str]:
    paths = list((root / "src/aletheia_lab/model_gateway").rglob("*.py"))
    paths += list((root / "src/aletheia_lab/evaluation/_attempt_store").rglob("*.py"))
    for pattern in (
        "claim_validation_v3*.py",
        "claim_corpus_live*.py",
        "claim_corpus_terminal_reader.py",
        "claim_corpus_normalization_recovery.py",
        "claim_corpus_adapters.py",
        "claim_corpus_contracts.py",
        "claim_corpus_readiness.py",
        "claim_evidence_semantics.py",
        "claim_validation_v2_runtime*.py",
        "claim_validation_v2_relation_frames.py",
        "claim_validation_v2_frames.py",
        "claim_validation_v2_qualification.py",
        "variant_fairness.py",
        "execution_contracts.py",
    ):
        paths += list((root / "src/aletheia_lab/evaluation").glob(pattern))
    paths += [
        root / p
        for p in (
            "scripts/claim_support_validation_v3.py",
            "src/aletheia_lab/filesystem.py",
            "src/aletheia_lab/project/identity.py",
            "src/aletheia_lab/diagnosis/variant_registry.py",
            FAIRNESS_PATH,
        )
    ]
    return {
        p.relative_to(root).as_posix(): content_sha256(p.read_bytes()) for p in sorted(set(paths))
    }
