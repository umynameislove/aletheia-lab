from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import numpy as np
import pytest

from aletheia_lab.evaluation import serving_large_model_review as review


def test_full_vectors_not_just_top_one_and_locked_tolerance():
    reference = np.zeros((1, 1000), dtype=np.float32)
    observed = reference.copy()
    observed[0, 999] = 0.001
    result = review.numerical_comparison(reference, observed)
    assert result["shape_ok"] and result["finite"]
    assert not result["full_vector_tolerance_passed"]
    observed[0, 999] = 1e-6
    assert review.numerical_comparison(reference, observed)["full_vector_tolerance_passed"]


@pytest.mark.parametrize("case", ["shape", "nan", "infinity"])
def test_nonfinite_or_wrong_shape_does_not_qualify(case):
    reference = np.zeros((1, 1000), dtype=np.float32)
    observed = reference.copy()
    if case == "shape":
        observed = observed[:, :1]
    else:
        observed[0, 0] = np.nan if case == "nan" else np.inf
    assert not review.numerical_comparison(reference, observed)["full_vector_tolerance_passed"]


def test_evidence_paths_cannot_escape_or_use_links(tmp_path: Path):
    with pytest.raises(ValueError, match="escapes"):
        review._owned(tmp_path, "../other")
    (tmp_path / "link").symlink_to(tmp_path / "missing")
    with pytest.raises(ValueError, match="symlinks"):
        review._owned(tmp_path, "link")


def test_unjoined_or_pending_wal_receipts_are_not_dropped(tmp_path: Path):
    path = tmp_path / "receipts.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE calls (request_id TEXT)")
    connection.commit()
    connection.close()
    with pytest.raises(ValueError, match="unjoined"):
        review._receipt_rows(tmp_path)
    Path(str(path) + "-wal").write_bytes(b"pending")
    with pytest.raises(ValueError, match="closed checkpointed"):
        review._receipt_rows(tmp_path)


def test_changed_qualification_threshold_is_not_accepted(tmp_path: Path):
    (tmp_path / "qualification-spec.json").write_text(
        json.dumps(
            {"qualification": {"rtol": 1.0, "atol": 1e-5, "expected_output_shape": [1, 1000]}}
        )
    )
    with pytest.raises(ValueError, match="tolerance"):
        review._qualification(tmp_path, {})


def test_rehashed_wrong_serving_output_still_fails_reference(tmp_path: Path):
    (tmp_path / "outputs").mkdir()
    np.save(tmp_path / "outputs/request-0.npy", np.ones((1, 1000)), allow_pickle=False)
    np.save(
        tmp_path / "outputs/qualification-torch-zero.npy", np.zeros((1, 1000)), allow_pickle=False
    )
    with pytest.raises(ValueError, match="export tolerance"):
        review._check_output(tmp_path, 0, "zero")
