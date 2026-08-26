"""Property checks for canonical mapping identity and reconciliation."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from aletheia_lab.project.mapping import (
    DatasetTargetMapping,
    MetricSourceMapping,
    RunMapping,
    build_project_mapping_configuration,
)

pytestmark = pytest.mark.property

_PROJECT = "p3-project-" + "1" * 64
_BUNDLE = "p3-bundle-" + "2" * 64
_COLLECTION = "3" * 64
_DATASET = "p3-item-" + "4" * 64
_METRICS_A = "p3-item-" + "5" * 64
_METRICS_B = "p3-item-" + "6" * 64


def _target() -> DatasetTargetMapping:
    return DatasetTargetMapping(
        mapping_id="target", project_item_id=_DATASET, target_field="label", identifier_field="id"
    )


def _metric(mapping_id: str, item_id: str) -> MetricSourceMapping:
    return MetricSourceMapping(
        mapping_id=mapping_id,
        project_item_id=item_id,
        format="csv",
        metric_name_field="name",
        metric_value_field="value",
        run_id_field="run",
    )


@settings(max_examples=20)
@given(reverse_metrics=st.booleans(), reverse_runs=st.booleans())
def test_mapping_identity_is_invariant_to_input_order(
    reverse_metrics: bool, reverse_runs: bool
) -> None:
    metrics = (_metric("a", _METRICS_A), _metric("b", _METRICS_B))
    runs = (RunMapping(run_id="baseline"), RunMapping(run_id="candidate"))
    first = build_project_mapping_configuration(
        project_id=_PROJECT,
        project_bundle_id=_BUNDLE,
        file_collection_sha256=_COLLECTION,
        target=_target(),
        metric_sources=tuple(reversed(metrics)) if reverse_metrics else metrics,
        runs=tuple(reversed(runs)) if reverse_runs else runs,
        baseline_run_id="baseline",
    )
    canonical = build_project_mapping_configuration(
        project_id=_PROJECT,
        project_bundle_id=_BUNDLE,
        file_collection_sha256=_COLLECTION,
        target=_target(),
        metric_sources=metrics,
        runs=runs,
        baseline_run_id="baseline",
    )

    assert first == canonical
    assert first.mapping_sha256 == canonical.mapping_sha256
