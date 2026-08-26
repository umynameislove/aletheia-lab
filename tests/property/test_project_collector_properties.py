"""Generative invariants for metadata-only project collection."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from aletheia_lab.project import collect_project_files, grant_project_root, import_local_project

pytestmark = pytest.mark.property

_CELL = st.text(
    alphabet=st.characters(whitelist_categories=("Ll", "Lu", "Nd")), min_size=1, max_size=16
).filter(lambda value: value not in {"id", "value"})


@settings(max_examples=30)
@given(first=_CELL, second=_CELL)
def test_csv_row_values_never_enter_dataset_metadata(first: str, second: str) -> None:
    first_value = f"ROW_A_{first}_END"
    second_value = f"ROW_B_{second}_END"
    raw = f"id,value\n1,{first_value}\n2,{second_value}\n"
    with TemporaryDirectory(prefix="project-collector-") as directory:
        root = Path(directory)
        (root / "dataset.csv").write_text(raw, encoding="utf-8")
        result = import_local_project(
            grant_project_root(root),
            display_name="Property fixture",
            ingested_at="2026-08-25T00:00:00Z",
        )
        assert result.bundle is not None

        collection = collect_project_files(result.bundle, result.artifacts)
        serialized = collection.model_dump_json()

        assert collection.observations[0].dataset is not None
        assert collection.observations[0].dataset.row_count == 2
        assert first_value not in serialized
        assert second_value not in serialized
