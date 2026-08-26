"""Property checks for metric-change identity and direction."""

from __future__ import annotations

from hypothesis import assume, given
from hypothesis import strategies as st

from aletheia_lab.project.identity import canonical_project_sha256
from aletheia_lab.project.mapping import MetricObservation
from aletheia_lab.project.regression import _metric_change

_ITEM = "p3-item-" + "1" * 64


def _observation(value: float) -> MetricObservation:
    value = 0.0 if value == 0 else value
    fields = {
        "source_mapping_id": "metrics",
        "project_item_id": _ITEM,
        "run_id": "candidate",
        "metric_name": "loss",
        "metric_value": value,
        "step": None,
        "source_record_index": 0,
    }
    return MetricObservation(
        observation_id=f"p3-metric-{canonical_project_sha256(fields)}", **fields
    )


@given(
    before=st.floats(min_value=-1_000_000, max_value=1_000_000, allow_nan=False),
    after=st.floats(min_value=-1_000_000, max_value=1_000_000, allow_nan=False),
)
def test_metric_change_direction_and_identity_are_deterministic(
    before: float, after: float
) -> None:
    assume(before != after)
    first = _metric_change(_observation(before), _observation(after))
    replay = _metric_change(_observation(before), _observation(after))

    assert first == replay
    assert first.kind == ("increased" if after > before else "decreased")
    assert first.delta == after - before


@given(value=st.floats(min_value=-1_000_000, max_value=1_000_000, allow_nan=False))
def test_metric_add_remove_never_fabricate_a_delta(value: float) -> None:
    observation = _observation(value)
    assert _metric_change(None, observation).delta is None
    assert _metric_change(observation, None).delta is None
