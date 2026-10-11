"""Synthetic checks for source-informed native inference transfer."""

from __future__ import annotations

import copy
import subprocess
from pathlib import Path

import pytest

from aletheia_lab.evaluation.incident_audit_transfer import (
    _child,
    dependency_span,
    expected_weights,
    make_frame,
    matrix_output,
    run,
)
from aletheia_lab.evaluation.request_model_audit import resolve


def test_multifile_dependency_uses_implicit_full_span() -> None:
    assert dependency_span({"location": "A.weight"}, 36) == (0, 36)
    assert dependency_span({"location": "all.weight", "offset": "36", "length": "36"}, 72) == (
        36,
        36,
    )
    with pytest.raises(ValueError, match="bounded owned"):
        dependency_span({"offset": "40", "length": "36"}, 72)


def test_upstream_matrix_reference_is_independent_of_resolver() -> None:
    values = [[float(i * 3 + j) for j in range(3)] for i in range(3)]
    weights = expected_weights()
    assert matrix_output(values, weights) == [
        [945000, 1015200, 1085400],
        [2905200, 3121200, 3337200],
        [4865400, 5227200, 5589000],
    ]
    raw = {
        "input": values,
        "weights": weights,
        "output": matrix_output(values, weights),
        "effective_proto_sha256": "a" * 64,
    }
    assert resolve(make_frame(0, raw, True)) == "compliant"
    changed = copy.deepcopy(raw)
    changed["weights"]["A"] = [[0.0] * 3 for _ in range(3)]
    changed["output"] = matrix_output(values, changed["weights"])
    assert resolve(make_frame(1, changed, True)) == "violation"
    assert resolve(make_frame(1, raw, False)) == "unknown"


def test_equal_output_does_not_repair_changed_effective_dependency() -> None:
    zeros = [[0.0] * 3 for _ in range(3)]
    raw = {
        "input": zeros,
        "weights": expected_weights(),
        "output": zeros,
        "effective_proto_sha256": "a" * 64,
    }
    changed = copy.deepcopy(raw)
    changed["weights"]["A"] = zeros
    assert matrix_output(zeros, changed["weights"]) == raw["output"]
    assert resolve(make_frame(1, changed, True)) == "violation"


def test_candidate_timeout_is_retained_not_discarded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def timeout(*args: object, **kwargs: object) -> object:
        raise subprocess.TimeoutExpired("worker", 60, output=b"partial", stderr=b"failure")

    monkeypatch.setattr(subprocess, "run", timeout)
    row = _child(tmp_path, tmp_path, "test_large_multi_files", "lawful")
    assert row["status"] == "worker_timeout"
    assert (tmp_path / "multi_files-lawful.stdout").read_bytes() == b"partial"


def test_candidate_will_not_write_into_public_or_existing_directory(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="fresh private"):
        run(tmp_path, tmp_path / "study")
    with pytest.raises(ValueError, match="fresh private"):
        run(tmp_path / "repo", tmp_path)
