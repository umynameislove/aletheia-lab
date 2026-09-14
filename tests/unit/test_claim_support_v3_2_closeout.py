"""Scientific and publication boundaries for V3.2 relation closeout."""

from __future__ import annotations

import copy
import io
import json
import zipfile
from collections import Counter
from pathlib import Path

import pytest

from aletheia_lab.evaluation import claim_support_v3_2_packets as packet_module
from aletheia_lab.evaluation.claim_support_v3_2_closeout import (
    Candidate,
    interpret_terminal,
    select_samples,
)
from aletheia_lab.evaluation.claim_support_v3_2_packets import (
    GUIDE_PATH,
    WORKFLOW_PATH,
    packet_artifacts,
    publish_artifacts,
    verify_artifacts,
)
from aletheia_lab.evaluation.claim_support_v3_2_relation_cohort import (
    build_relation_tasks,
    outbound,
)
from aletheia_lab.evaluation.claim_validation_v3_design import structural_relations
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256 as digest
from aletheia_lab.evaluation.human_workflow import load_human_workflow
from aletheia_lab.evaluation.instrument_validation import LABEL_ORDER, load_validation_protocol

ROOT = Path(__file__).resolve().parents[2]
FRAME_LABEL = {
    "natural": "fully_supported",
    "withdrawal": "unsupported",
    "partial": "partially_supported",
    "counter": "contradicted",
}


def _outcome(task: dict[str, object], accepted: bool) -> dict[str, object]:
    return {
        "task_sha256": task["task_sha256"],
        "gateway_request_identity_sha256": "a" * 64,
        "gateway_status": "parsed",
        "structurally_accepted": accepted,
        "semantic_issue_code": None if accepted else "relation_matrix_invalid",
        "attempt_count": 1,
        "failure_categories": {},
    }


def _designed_candidates() -> tuple[Candidate, ...]:
    result = []
    for task in build_relation_tasks(ROOT):
        result.append(
            Candidate(
                task=task,
                automatic_label=FRAME_LABEL[task["frame"]],  # type: ignore[arg-type]
                gateway_request_identity_sha256=digest(task["task_sha256"]),
                payload_sha256=digest({"task": task["task_sha256"]}),
            )
        )
    return tuple(result)


def test_interpretation_uses_relation_judgment_not_frame_intent() -> None:
    task = next(item for item in build_relation_tasks(ROOT) if item["frame"] == "counter")
    _, context = outbound(task)
    payload = structural_relations(task["claim"], context)
    for row in payload["relations"]:
        row["relation"] = "neutral"

    candidate, audit = interpret_terminal(task, _outcome(task, True), payload)

    assert candidate is not None
    assert candidate.automatic_label == "unsupported"
    assert audit["frame"] == "counter"
    assert audit["automatic_label"] == "unsupported"


def test_malformed_matrix_is_explained_but_never_repaired() -> None:
    task = next(
        item
        for item in build_relation_tasks(ROOT)
        if len(item["claim"]["material_parts"]) == 2 and len(item["framed_context"]["items"]) >= 2
    )
    _, context = outbound(task)
    payload = structural_relations(task["claim"], context)
    malformed = copy.deepcopy(payload)
    malformed["relations"][-1] = malformed["relations"][0]

    candidate, audit = interpret_terminal(task, _outcome(task, False), malformed)

    diagnostics = audit["matrix_diagnostics"]
    assert candidate is None
    assert diagnostics["duplicate_cell_count"] == 1
    assert len(diagnostics["missing_cells"]) == 1
    assert diagnostics["repair_performed"] is False
    assert diagnostics["rerun_performed"] is False


def test_fixed_sampler_builds_disjoint_balanced_real_sets() -> None:
    candidates = _designed_candidates()
    protocol = load_validation_protocol(
        ROOT / "configs/evaluation/claim_support_validation_protocol.json"
    )

    main, onboarding = select_samples(candidates, protocol)

    assert len(main) == 200
    assert len(onboarding) == 20
    assert Counter(item.automatic_label for item in main) == Counter(
        {label: 50 for label in LABEL_ORDER}
    )
    assert Counter(item.automatic_label for item in onboarding) == Counter(
        {label: 5 for label in LABEL_ORDER}
    )
    assert {item.claim_text for item in main}.isdisjoint(item.claim_text for item in onboarding)
    for sample, quota in ((main, 50), (onboarding, 5)):
        assert len({item.claim_text for item in sample}) == quota * len(LABEL_ORDER)
        for label in LABEL_ORDER:
            members = [item for item in sample if item.automatic_label == label]
            assert max(Counter(item.case_family_id for item in members).values()) <= 5
            assert max(Counter(item.output_id for item in members).values()) <= 2


def test_two_rater_surfaces_are_distinct_blank_and_leak_free() -> None:
    workflow = load_human_workflow(ROOT, Path(WORKFLOW_PATH))
    guide = (ROOT / GUIDE_PATH).read_bytes()
    supplement = (ROOT / "docs/claim-support-v3-2-rater-guide.md").read_bytes()
    files, mapping, packet_hashes = packet_artifacts(
        _designed_candidates()[:2],
        phase="main",
        workflow=workflow,
        guide=guide,
        supplement=supplement,
    )

    assert packet_hashes["rater_1"] != packet_hashes["rater_2"]
    packets = []
    for slot in workflow.rater_slots:
        payload = json.loads(files[f"held-main/source/{slot}/blind-packet.json"])
        packets.append(payload)
        assert payload["rater_slot"] == slot
        assert all(decision["support_label"] is None for decision in payload["decisions"])
        wire = files[f"held-main/source/{slot}/blind-packet.json"].decode()
        for forbidden in (
            "automatic_label",
            "source_variant",
            "family_id",
            'frame"',
            "gateway_request_identity_sha256",
            "payload_sha256",
        ):
            assert forbidden not in wire
        with zipfile.ZipFile(io.BytesIO(files[f"held-main/deliveries/{slot}.zip"])) as archive:
            assert set(archive.namelist()) == {
                "JOB.md",
                "RATER_GUIDE.md",
                "V3_2_SUPPLEMENT.md",
                "blind-packet.json",
                "submission-template.json",
            }
            assert (
                archive.read("blind-packet.json")
                == files[f"held-main/source/{slot}/blind-packet.json"]
            )
    assert [item["blind_claim_id"] for item in packets[0]["claims"]] == [
        item["blind_claim_id"] for item in packets[1]["claims"]
    ]
    assert len(mapping.entries) == 2
    assert {item.automatic_label for item in mapping.entries}


def test_private_bundle_isolates_evaluator_labels_from_onboarding_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = _designed_candidates()
    protocol = load_validation_protocol(
        ROOT / "configs/evaluation/claim_support_validation_protocol.json"
    )
    main, onboarding = select_samples(pool, protocol)
    report = {
        "selection_blocker": None,
        "closeout_sha256": "a" * 64,
        "estimand": {"target": "human agreement with frozen relation reduction"},
    }
    monkeypatch.setattr(
        packet_module,
        "build_closeout",
        lambda _root, _run, _qualification: (report, pool, main, onboarding),
    )

    files = packet_module.build_artifacts(ROOT, Path("unused"), Path("unused"))

    assert "sealed-evaluator/main-mapping.json" in files
    assert "sealed-evaluator/labeled-pool.json" in files
    assert not any(path.endswith("onboarding-mapping.json") for path in files)
    coordinator_files = {
        path: content for path, content in files.items() if not path.startswith("sealed-evaluator/")
    }
    assert all(b'"automatic_label":' not in content for content in coordinator_files.values())
    template = json.loads(files["coordinator-private/onboarding-reference-template.json"])
    assert template["claim_count"] == 20
    assert len(template["entries"]) == 20
    assert len({item["blind_claim_id"] for item in template["entries"]}) == 20
    assert all(item["reference_label"] is None for item in template["entries"])


def test_private_directory_publication_is_create_only_and_verified(tmp_path: Path) -> None:
    destination = tmp_path / "private-packets"
    expected = {"manifest.json": b"same bytes\n", "rater/packet.json": b"{}\n"}

    assert publish_artifacts(destination, expected) == "created"
    assert publish_artifacts(destination, expected) == "identical"
    verify_artifacts(destination, expected)
    (destination / "manifest.json").write_bytes(b"changed\n")
    with pytest.raises(ValueError, match="differ"):
        verify_artifacts(destination, expected)
