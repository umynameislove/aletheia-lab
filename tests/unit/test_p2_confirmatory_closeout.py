"""Immutable registration and atomic closeout-store contract tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from aletheia_lab.benchmark.p2.confirmatory_closeout import (
    ProtocolRegistrationReceipt,
    registration_from_github_release,
)
from aletheia_lab.benchmark.p2.confirmatory_execution import ConfirmatoryExecutionError
from aletheia_lab.benchmark.p2.confirmatory_protocol import load_confirmatory_protocol

_TAGGED_COMMIT = "eaa16121e8c42b846fbc80eaadb77bf4ab157a08"


def _payload(*, immutable: bool = True) -> dict[str, object]:
    return {
        "id": 12345,
        "tag_name": "p2-label-noise-confirmatory-v2",
        "html_url": (
            "https://github.com/umynameislove/aletheia-lab/releases/tag/"
            "p2-label-noise-confirmatory-v2"
        ),
        "created_at": "2026-08-17T06:39:07Z",
        "published_at": "2026-08-17T07:00:00Z",
        "immutable": immutable,
        "draft": False,
        "prerelease": False,
    }


def test_github_release_registration_requires_immutable_publication() -> None:
    protocol = load_confirmatory_protocol()
    receipt = registration_from_github_release(
        protocol=protocol,
        tagged_protocol_commit=_TAGGED_COMMIT,
        payload=_payload(),
    )

    assert receipt.protocol_sha256 == protocol.canonical_sha256()
    assert receipt.immutable
    assert receipt.release_published_at < datetime.now(UTC)

    with pytest.raises(ConfirmatoryExecutionError, match="immutable"):
        registration_from_github_release(
            protocol=protocol,
            tagged_protocol_commit=_TAGGED_COMMIT,
            payload=_payload(immutable=False),
        )


@pytest.mark.parametrize(
    "field",
    ["tag_name", "html_url", "published_at", "immutable", "draft", "prerelease"],
)
def test_registration_rejects_missing_release_evidence(field: str) -> None:
    payload = _payload()
    del payload[field]
    with pytest.raises(ConfirmatoryExecutionError, match="incomplete"):
        registration_from_github_release(
            protocol=load_confirmatory_protocol(),
            tagged_protocol_commit=_TAGGED_COMMIT,
            payload=payload,
        )


def test_registration_timestamp_must_precede_execution() -> None:
    protocol = load_confirmatory_protocol()
    now = datetime.now(UTC)
    with pytest.raises(ValueError, match="publication"):
        ProtocolRegistrationReceipt(
            protocol_sha256=protocol.canonical_sha256(),
            tag_name="p2-label-noise-confirmatory-v2",
            tagged_protocol_commit=_TAGGED_COMMIT,
            release_url=(
                "https://github.com/umynameislove/aletheia-lab/releases/tag/"
                "p2-label-noise-confirmatory-v2"
            ),
            release_id=1,
            release_created_at=now,
            release_published_at=now - timedelta(seconds=1),
            immutable=True,
            draft=False,
            prerelease=False,
        )


def test_registration_model_rejects_wrong_repository_release() -> None:
    payload = _payload()
    payload["html_url"] = "https://github.com/other/project/releases/tag/v2"
    with pytest.raises(ConfirmatoryExecutionError, match="immutable"):
        registration_from_github_release(
            protocol=load_confirmatory_protocol(),
            tagged_protocol_commit=_TAGGED_COMMIT,
            payload=payload,
        )
