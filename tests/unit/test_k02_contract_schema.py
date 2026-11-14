"""Check live responses against current models and keep frozen v7 fixture checks."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker, ValidationError
from test_k02_hosted_application import CAP_PATH, _request
from test_k02_read_backend import _backend

from aletheia_lab.hosted_contract.session_capabilities import (
    Capabilities,
    Problem,
    Session,
    problem_consumer_schema,
)

FROZEN_V7 = Path(__file__).resolve().parents[2] / "fixtures" / "j01" / "producer-review-v7"


@pytest.mark.parametrize(
    "route,state,expected_status,model",
    [
        ("/v1/session", "active", 200, Session),
        (CAP_PATH, "active", 200, Capabilities),
        ("/v1/session", "revoked", 401, Problem),
        (CAP_PATH, "membership_revoked", 403, Problem),
    ],
)
def test_read_responses_match_current_j01_models(route, state, expected_status, model):
    backend, sessions, workspaces = _backend()
    if state == "revoked":
        sessions.record = replace(sessions.record, state="revoked")
    elif state == "membership_revoked":
        workspaces.active = False

    status, _, body = _request(backend, route)
    assert status == expected_status
    value = json.loads(body)
    producer = Draft202012Validator(model.model_json_schema(), format_checker=FormatChecker())
    producer.validate(value)
    with pytest.raises(ValidationError):
        producer.validate({**value, "unexpected_contract_field": True})
    if model is Session:
        assert type(value["can_create_workspace"]) is bool
    if model is Problem:
        assert value["code"] == ("session_revoked" if state == "revoked" else "membership_revoked")
        Draft202012Validator(problem_consumer_schema(), format_checker=FormatChecker()).validate(value)


@pytest.mark.parametrize(
    "fixture_name,schema_name",
    [
        ("session.json", "session.schema.json"),
        ("capabilities.json", "capabilities.schema.json"),
        ("problem-session-revoked.json", "problem-producer.schema.json"),
        ("problem-membership-revoked.json", "problem-producer.schema.json"),
    ],
)
def test_frozen_v7_fixtures_still_match_their_published_schemas(fixture_name, schema_name):
    value = json.loads((FROZEN_V7 / fixture_name).read_text(encoding="utf-8"))
    schema = json.loads((FROZEN_V7 / schema_name).read_text(encoding="utf-8"))
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(value)
    if schema_name == "problem-producer.schema.json":
        consumer = json.loads((FROZEN_V7 / "problem-consumer.schema.json").read_text(encoding="utf-8"))
        Draft202012Validator(consumer, format_checker=FormatChecker()).validate(value)
