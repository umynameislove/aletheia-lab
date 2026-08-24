"""Fail-closed contracts for the v3 technical recovery."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from aletheia_lab.benchmark.p2.confirmatory_v3_recovery import (
    V3TechnicalFailureReceipt,
    load_v3_technical_failure_receipt,
    refuse_v3_1_reexecution,
    verify_v3_technical_failure_receipt,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_runtime import (
    V3RuntimeError,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _registration_payload() -> dict[str, object]:
    return {
        "schema_version": "p2-v3-registration/1",
        "protocol_sha256": (
            "0e9c594a6453dc111def3208582cec85d13518d542a61d86197620f9707ab7b2"
        ),
        "tag_name": "p2-label-noise-shift-factorial-v3.1",
        "tagged_protocol_commit": "0e05e3e2f94f870015648d8e81e1e8ae60985a20",
        "release_url": (
            "https://github.com/umynameislove/aletheia-lab/releases/tag/"
            "p2-label-noise-shift-factorial-v3.1"
        ),
        "release_id": 374260738,
        "release_created_at": "2026-08-21T08:29:24Z",
        "release_published_at": "2026-08-21T08:31:23Z",
        "immutable": True,
        "draft": False,
        "prerelease": False,
    }


def _local_evidence(tmp_path: Path) -> tuple[Path, Path]:
    registration = tmp_path / "registration.json"
    registration.write_text(
        json.dumps(_registration_payload(), indent=2) + "\n",
        encoding="utf-8",
    )
    marker = tmp_path / "sealed-open.json"
    marker.write_text(
        json.dumps(
            {
                "execution_commit": "1d365f22c133ce5d70d3ac13b465bfb6202d6e50",
                "opened_at": "2026-08-22T20:41:21.565913+00:00",
                "protocol_sha256": (
                    "0e9c594a6453dc111def3208582cec85d13518d542a61d86197620f9707ab7b2"
                ),
                "registration_sha256": (
                    "592ef64ec3fcf25cd6ab266447ef007b22ec7a0e46969cacad848812163dbc0a"
                ),
                "rerun_forbidden": True,
                "schema_version": "p2-v3-sealed-open/1",
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    return registration, marker


def test_tracked_failure_receipt_preserves_exact_attempt_and_four_cell_census() -> None:
    receipt = load_v3_technical_failure_receipt()
    assert receipt.rerun_forbidden
    assert not receipt.result_store_published
    assert not receipt.scientific_disposition_generated
    assert len(receipt.affected_cell_census) == 4
    assert {
        (item.direction, item.conditional_rate, item.corruption_seed)
        for item in receipt.affected_cell_census
    } == {
        ("yes_to_no", 0.1, 6103),
        ("yes_to_no", 0.1, 6111),
        ("yes_to_no", 0.1, 6112),
        ("no_to_yes", 0.3, 6118),
    }
    assert all(item.mean_gradient_infinity_norm < 1e-8 for item in receipt.affected_cell_census)


def test_tracked_failure_receipt_permanently_blocks_v3_1_reexecution() -> None:
    with pytest.raises(V3RuntimeError, match="permanently retired"):
        refuse_v3_1_reexecution(root=Path("."))


def test_failure_receipt_binds_registration_marker_and_absent_store(tmp_path: Path) -> None:
    registration, marker = _local_evidence(tmp_path)
    tracked = load_v3_technical_failure_receipt()
    receipt = V3TechnicalFailureReceipt.model_validate(
        {
            **tracked.model_dump(),
            "registration_file_sha256": _sha256(registration),
            "sealed_marker_file_sha256": _sha256(marker),
        }
    )
    assert (
        verify_v3_technical_failure_receipt(
            receipt,
            root=tmp_path,
            registration_path=registration,
            marker_path=marker,
            result_store_path=tmp_path / "result-store",
        )
        == receipt
    )

    (tmp_path / "result-store").mkdir()
    with pytest.raises(V3RuntimeError, match="existing result store"):
        verify_v3_technical_failure_receipt(
            receipt,
            root=tmp_path,
            registration_path=registration,
            marker_path=marker,
            result_store_path=tmp_path / "result-store",
        )


def test_failure_receipt_rejects_reclassified_or_incomplete_cell_census() -> None:
    receipt = load_v3_technical_failure_receipt()
    payload = receipt.model_dump()
    payload["affected_cell_census"] = payload["affected_cell_census"][:-1]
    with pytest.raises(ValueError, match="four audited cells"):
        V3TechnicalFailureReceipt.model_validate(payload)

    payload = receipt.model_dump()
    payload["result_store_published"] = True
    with pytest.raises(ValueError):
        V3TechnicalFailureReceipt.model_validate(payload)

