"""Native-control implications need source assumptions, not future gold."""

import pytest

from aletheia_lab.evaluation.litserve_evidence_closeout import (
    compare_source_contract,
    lawful_status_counts,
    native_response_contract,
)


def row(status=200, body=None, error=None):
    return {
        "token": "t",
        "status": status,
        "body": {"output": 1.0} if body is None else body,
        "error": error,
        "endpoint": "/a",
        "x": 0.0,
    }


def test_native_contract_can_be_sufficient_without_collector_receipts():
    assert native_response_contract(row(), synchronous_immutable_contract=True) == {
        "origin": "compliant",
        "closure": "closed",
    }


def test_equal_output_is_not_identity_proof_without_immutable_source_contract():
    assert native_response_contract(row(), synchronous_immutable_contract=False) == {
        "origin": "unknown",
        "closure": "unknown",
    }


@pytest.mark.parametrize(
    "detail", ["Request timed out", "cooperative deadline expired before designated computation"]
)
def test_known_synchronous_refusal_is_closed_but_not_success(detail):
    assert native_response_contract(
        row(504, {"detail": detail}), synchronous_immutable_contract=True
    ) == {"origin": "unavailable", "closure": "closed"}


@pytest.mark.parametrize("detail", ["upstream timeout", "Request timed out"])
def test_untrusted_or_proxy_timeout_never_infers_worker_closure(detail):
    assert (
        native_response_contract(
            row(504, {"detail": detail}), synchronous_immutable_contract=False
        )["closure"]
        == "unknown"
    )


def test_unknown_timeout_or_client_abandonment_is_not_attempt_closure():
    for r in [row(None, {}, "ReadTimeout"), row(504, {"detail": "upstream timeout"})]:
        assert (
            native_response_contract(r, synchronous_immutable_contract=True)["closure"] == "unknown"
        )


def test_numeric_mismatch_does_not_certify_compliance():
    assert (
        native_response_contract(row(200, {"output": 9}), synchronous_immutable_contract=True)[
            "origin"
        ]
        == "unknown"
    )


def test_comparison_rejects_misaligned_reference():
    with pytest.raises(ValueError, match="census"):
        compare_source_contract({"rows": [row()]}, {"rows": [{"token": "other"}]})


def test_lawful_family_mismatch_cannot_silently_return_empty_counts():
    with pytest.raises(ValueError, match="census"):
        lawful_status_counts([{"arm": "native", "rows": [{"family": "wrong", "status": 200}] * 6}])
    sources = [
        {"arm": "native", "rows": [{"family": "ordered_endpoint_batches", "status": 200}] * 6}
    ]
    assert lawful_status_counts(sources) == {"native": {"200": 6}, "cooperative": {}}


def test_unidentified_http_500_is_not_worker_terminal_proof():
    assert (
        native_response_contract(
            row(500, {"detail": "error"}), synchronous_immutable_contract=True
        )["closure"]
        == "unknown"
    )
