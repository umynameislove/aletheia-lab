import pytest

from aletheia_lab.evaluation.agreement import cohens_kappa
from aletheia_lab.evaluation.metrics import binary_score, divergence_label, mean


def test_binary_score() -> None:
    assert binary_score(True) == 1.0
    assert binary_score(False) == 0.0


def test_mean_rejects_empty_iterable() -> None:
    with pytest.raises(ValueError):
        mean([])


def test_divergence_label() -> None:
    assert divergence_label(True, False) == "faithful_but_wrong"
    assert divergence_label(False, True) == "correct_but_unfaithful"


def test_cohens_kappa_perfect_agreement() -> None:
    assert cohens_kappa(["yes", "no", "yes"], ["yes", "no", "yes"]) == 1.0
