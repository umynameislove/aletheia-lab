"""Historical state decisions are grounded in the admitted observation and original contract."""

import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from aletheia_lab.hosted.historical_state_evidence import (
    HistoricalStateInput,
    evaluate_historical_state,
)

_FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "historical-state" / "v1" / "replay.json"


@pytest.fixture
def replay_pair():
    return json.loads(_FIXTURE.read_bytes())


def _parse(receipt, contract):
    return HistoricalStateInput.model_validate_json(json.dumps({"receipt": receipt, "contract": contract}))


def test_replay_pair_varies_actual_state_with_fixed_coarse_view(replay_pair):
    cases = replay_pair["cases"]
    contract = replay_pair["contract"]
    held = _parse(cases["held-A-denied"], contract)
    current = _parse(cases["B-authorized"], contract)
    assert held.receipt.output == current.receipt.output == (4.0, 3.0)
    assert held.receipt.graph_sha256 == current.receipt.graph_sha256
    assert held.receipt.current_alias == current.receipt.current_alias == "B"
    assert held.receipt.request_id == current.receipt.request_id
    assert held.receipt.operation_id != current.receipt.operation_id
    assert held.receipt.observation != current.receipt.observation
    assert evaluate_historical_state(held).verdict == "denied"
    assert evaluate_historical_state(current).verdict == "allowed"
    assert evaluate_historical_state(_parse(cases["B-authorized"] | {"current_alias": "C"}, contract)) == evaluate_historical_state(current)


def test_evidence_and_contract_absence_have_distinct_meanings(replay_pair):
    receipt = replay_pair["cases"]["B-authorized"]
    contract = replay_pair["contract"]
    missing_state = _parse(receipt | {"observation": None}, contract)
    missing_contract = _parse(receipt, None)
    unsupported = _parse(receipt | {"consumer_id": "other-consumer"}, contract | {"consumer_id": "other-consumer"})
    assert evaluate_historical_state(missing_state).verdict == "unknown"
    assert evaluate_historical_state(missing_contract).verdict == "underdefined"
    assert evaluate_historical_state(unsupported).verdict == "unsupported"


@pytest.mark.parametrize("key,value", [
    ("output", [True, 3]),
    ("output", [float("inf"), 3]),
    ("output", [4, 3, 9]),
    ("source_receipt_sha256", "not-a-sha256"),
    ("execution_mode", "native-gpu"),
    ("schema_version", "historical-state-receipt/v99"),
    ("verdict", "allowed"),
])
def test_malformed_receipt_never_enters_decision(replay_pair, key, value):
    receipt = replay_pair["cases"]["B-authorized"] | {key: value}
    with pytest.raises(ValidationError):
        _parse(receipt, replay_pair["contract"])


def test_receipt_cannot_choose_another_consumer_under_a_pinned_contract(replay_pair):
    contract = copy.deepcopy(replay_pair["contract"])
    contract["consumer_id"] = "state-authorization-reader"
    with pytest.raises(ValidationError, match="historical consumer"):
        _parse(replay_pair["cases"]["B-authorized"], contract)
