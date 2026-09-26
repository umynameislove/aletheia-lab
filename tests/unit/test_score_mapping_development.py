"""Fail-closed development-only source and output guards."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from aletheia_lab.benchmark.p2.score_mapping_development import (
    ScoreMappingDevelopmentError,
    _fit_reference_model,
    _private_output_guard,
)


def test_private_output_must_be_new_and_outside_repository(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    private = tmp_path / "private"
    private.mkdir()
    _private_output_guard(root, private / "new")
    with pytest.raises(ScoreMappingDevelopmentError):
        _private_output_guard(root, root / "internal")
    existing = private / "existing"
    existing.mkdir()
    with pytest.raises(ScoreMappingDevelopmentError):
        _private_output_guard(root, existing)
    link = private / "link"
    link.symlink_to(root / "internal")
    with pytest.raises(ScoreMappingDevelopmentError):
        _private_output_guard(root, link)


@pytest.mark.parametrize("kind", ["logistic_regression", "hist_gradient_boosting"])
def test_reference_model_has_two_ordered_source_columns(kind: str) -> None:
    x = np.asarray([[float(index), float(index % 3)] for index in range(60)])
    y = tuple(index % 2 for index in range(60))
    model = _fit_reference_model(kind, x, y)  # type: ignore[arg-type]
    scores = model.predict_proba(x)
    assert tuple(model.classes_) == (0, 1)
    assert scores.shape == (60, 2)
    np.testing.assert_allclose(scores.sum(axis=1), 1.0, atol=1e-12, rtol=0)


def test_unknown_model_kind_fails_closed() -> None:
    with pytest.raises(ScoreMappingDevelopmentError):
        _fit_reference_model("other", np.ones((4, 2)), (0, 1, 0, 1))  # type: ignore[arg-type]
