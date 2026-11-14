"""Check the proposed producer boundary with synthetic data only."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from aletheia_lab.hosted_contract.session_capabilities import (
    PROBLEM_STATUS,
    Capabilities,
    Problem,
    Session,
    problem_consumer_schema,
)

ROOT = Path(__file__).resolve().parents[2]


def _fixture(name: str) -> dict:
    return json.loads((ROOT / "fixtures" / "j01" / name).read_text(encoding="utf-8"))


def test_session_producer_roundtrip_and_rejects_wrong_version() -> None:
    source = _fixture("session-proposal.json")
    session = Session.model_validate_json(json.dumps(source))
    produced = json.loads(session.model_dump_json())
    assert produced == {**source, "can_create_workspace": False}
    source["schema_version"] = "product-api/v2"
    with pytest.raises(ValidationError):
        Session.model_validate_json(json.dumps(source))


def test_capabilities_scope_and_untrusted_values() -> None:
    source = _fixture("capabilities-proposal.json")
    value = Capabilities.model_validate_json(json.dumps(source))
    value.require_workspace("ws_synthetic_001")
    with pytest.raises(ValueError, match="workspace"):
        value.require_workspace("ws_other")
    source["membership"]["revision"] = -1
    with pytest.raises(ValidationError):
        Capabilities.model_validate_json(json.dumps(source))


def test_unknown_fields_and_nonpositive_limits_are_rejected() -> None:
    source = _fixture("capabilities-proposal.json")
    source["unannounced_field"] = "unexpected"
    with pytest.raises(ValidationError):
        Capabilities.model_validate_json(json.dumps(source))
    del source["unannounced_field"]
    source["limits"]["page_max"] = 0
    with pytest.raises(ValidationError):
        Capabilities.model_validate_json(json.dumps(source))


@pytest.mark.parametrize(
    "name", [
        "problem-session-expired-proposal.json",
        "problem-session-revoked-proposal.json",
        "problem-membership-revoked-proposal.json",
        "problem-permission-denied-proposal.json",
        "problem-csrf-invalid-proposal.json",
    ]
)
def test_problem_roundtrip_and_rejects_success_status(name: str) -> None:
    source = _fixture(name)
    problem = Problem.model_validate_json(json.dumps(source))
    assert json.loads(problem.model_dump_json()) == source
    source["status"] = 200
    with pytest.raises(ValidationError):
        Problem.model_validate_json(json.dumps(source))


def test_problem_rejects_mismatched_status_for_known_code() -> None:
    source = _fixture("problem-membership-revoked-proposal.json")
    source["status"] = 401
    with pytest.raises(ValidationError):
        Problem.model_validate_json(json.dumps(source))


@pytest.mark.parametrize("code", ["Bad_code", "bad-code", "bad.code", "9bad", "a" * 65])
def test_problem_rejects_invalid_codes(code: str) -> None:
    source = _fixture("problem-session-expired-proposal.json")
    source["code"] = code
    with pytest.raises(ValidationError):
        Problem.model_validate_json(json.dumps(source))


def test_problem_detail_limit() -> None:
    source = _fixture("problem-session-expired-proposal.json")
    source["detail"] = "x" * 1000
    Problem.model_validate_json(json.dumps(source))
    source["detail"] += "x"
    with pytest.raises(ValidationError):
        Problem.model_validate_json(json.dumps(source))


def test_capabilities_export_format_enum() -> None:
    source = _fixture("capabilities-proposal.json")
    source["supported_export_formats"] = ["json", "markdown", "pdf"]
    Capabilities.model_validate_json(json.dumps(source))
    source["supported_export_formats"].append("docx")
    with pytest.raises(ValidationError):
        Capabilities.model_validate_json(json.dumps(source))


@pytest.mark.parametrize(("code", "status"), PROBLEM_STATUS.items())
def test_problem_statuses_are_consistent(code: str, status: int) -> None:
    source = _fixture("problem-session-expired-proposal.json")
    source.update(code=code, status=status)
    Problem.model_validate_json(json.dumps(source))
    source["status"] = 500
    with pytest.raises(ValidationError):
        Problem.model_validate_json(json.dumps(source))


def test_problem_consumer_schema_keeps_mapping_and_allows_extensions() -> None:
    schema = problem_consumer_schema()
    assert schema["additionalProperties"] is True
    constraints = schema["allOf"]
    assert len(constraints) == len(PROBLEM_STATUS)
    for clause in constraints:
        code = clause["if"]["properties"]["code"]["const"]
        assert clause["then"]["properties"]["status"]["const"] == PROBLEM_STATUS[code]
    assert Problem.model_json_schema()["additionalProperties"] is False


def test_session_rejects_invalid_calendar_date() -> None:
    source = _fixture("session-proposal.json")
    source["session"]["expires_at"] = "2026-02-30T12:00:00Z"
    with pytest.raises(ValidationError):
        Session.model_validate_json(json.dumps(source))
    schema = Session.model_json_schema()
    assert schema["$defs"]["SessionInfo"]["properties"]["expires_at"]["pattern"]
