from pathlib import Path

import pytest

from aletheia_lab.evaluation.claim_corpus_provider_audit import (
    audit_predecessor_provider_failures,
)


def test_missing_store_is_not_created(tmp_path: Path) -> None:
    destination = tmp_path / "absent"
    with pytest.raises(ValueError, match="real directory"):
        audit_predecessor_provider_failures(destination)
    assert not destination.exists()


def test_unknown_store_member_is_rejected_without_modification(tmp_path: Path) -> None:
    marker = tmp_path / "unexpected"
    marker.write_bytes(b"preserve")
    with pytest.raises(ValueError, match="membership"):
        audit_predecessor_provider_failures(tmp_path)
    assert marker.read_bytes() == b"preserve"


def test_empty_census_is_not_treated_as_success(tmp_path: Path) -> None:
    (tmp_path / "authorities").mkdir()
    (tmp_path / "requests").mkdir()
    with pytest.raises(ValueError, match="incomplete"):
        audit_predecessor_provider_failures(tmp_path)
