from __future__ import annotations

from collections import Counter
from dataclasses import replace
from itertools import product
from pathlib import Path

import pytest

from aletheia_lab.evaluation import model_load_provenance as baseline
from aletheia_lab.evaluation.model_load_contract import (
    LoadContract,
    Observation,
    Record,
    Scope,
    completion_monitor,
    receipt_checker,
)

pytest.importorskip("in_toto", reason="requires the pinned provenance optional extra")
A, B = "a" * 64, "b" * 64
SCOPE = Scope("request", 0)
CONTRACT = LoadContract("pin_at_acceptance", (A, B))
SELECT = Record("selection", SCOPE, "selection", A, "token", 1, "pin_at_acceptance")
LOAD = Record("load", SCOPE, "load", A, "token")
CLOSE = Record("close", SCOPE, "closure", load_count=1)


@pytest.mark.parametrize(
    ("records", "verdict", "eligibility"),
    [
        ((SELECT, LOAD, CLOSE), "compliant", "load"),
        ((SELECT, replace(LOAD, digest=B), CLOSE), "violation", "load"),
        ((SELECT, replace(LOAD, digest=B)), "violation", "load"),
        ((SELECT, LOAD), "unknown", "load"),
        ((SELECT, CLOSE), "unknown", "load"),
        ((LOAD, CLOSE), "unknown", "load"),
        ((), "unknown", "undetermined"),
        ((SELECT,), "unknown", "undetermined"),
        ((replace(CLOSE, load_count=2),), "violation", "load"),
        ((LOAD, replace(LOAD, identifier="second")), "violation", "load"),
        ((SELECT, LOAD, replace(LOAD, identifier="second")), "violation", "load"),
        ((replace(SELECT, phase="resolve_at_load"), CLOSE), "violation", "load"),
        ((replace(SELECT, phase="resolve_at_load"), LOAD), "violation", "load"),
        ((SELECT, LOAD, replace(LOAD, identifier="second"), CLOSE), "conflict", "load"),
        ((SELECT, replace(LOAD, selection="other"), CLOSE), "conflict", "load"),
        ((SELECT, replace(LOAD, digest="c" * 64), CLOSE), "conflict", "load"),
        ((SELECT, replace(SELECT, identifier="duplicate"), LOAD, LOAD, CLOSE), "compliant", "load"),
        ((SELECT, replace(SELECT, digest=B), LOAD, CLOSE), "conflict", "load"),
        ((SELECT, replace(SELECT, identifier="other", digest=B), LOAD, CLOSE), "conflict", "load"),
        (
            (SELECT, LOAD, CLOSE, replace(CLOSE, identifier="other", load_count=2)),
            "conflict",
            "load",
        ),
        ((SELECT, LOAD, replace(CLOSE, load_count=0)), "conflict", "load"),
        ((SELECT, LOAD, replace(CLOSE, load_count=3)), "conflict", "load"),
        ((SELECT, replace(CLOSE, load_count=0)), None, "no_new_load"),
        ((SELECT, LOAD, CLOSE, replace(LOAD, scope=Scope("foreign", 0))), "compliant", "load"),
    ],
    ids=[
        "complete",
        "wrong",
        "wrong-prefix",
        "matching-prefix",
        "missing-buffer",
        "missing-selection",
        "empty",
        "selection-only",
        "hidden-extra",
        "extra-no-selection",
        "extra-prefix",
        "wrong-phase-no-buffer",
        "wrong-phase-prefix",
        "closure-conflict",
        "token-conflict",
        "domain-conflict",
        "semantic-duplicate",
        "identity-conflict",
        "selection-conflict",
        "receipt-conflict",
        "zero-with-load",
        "outside-bound",
        "cache",
        "foreign",
    ],
)
def test_real_signed_policy_adapter_matches_strong_baselines_without_calling_them(
    records: tuple[Record, ...],
    verdict: str | None,
    eligibility: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observation = Observation(CONTRACT, SCOPE, records)
    expected_s, expected_t = receipt_checker(observation), completion_monitor(observation)

    def forbidden(*_: object) -> None:
        raise AssertionError("provenance adapter cannot call a candidate checker")

    monkeypatch.setattr("aletheia_lab.evaluation.model_load_contract.receipt_checker", forbidden)
    monkeypatch.setattr("aletheia_lab.evaluation.model_load_contract.completion_monitor", forbidden)
    result = baseline.provenance_checker(observation, tmp_path / "signed")
    assert (result.decision.verdict, result.decision.eligibility) == (verdict, eligibility)
    assert (expected_s.verdict, expected_s.eligibility) == (verdict, eligibility)
    assert (expected_t.verdict, expected_t.eligibility) == (verdict, eligibility)


@pytest.mark.parametrize("retry", ["inherit", "reselect"])
@pytest.mark.parametrize(
    "parent_kind", ["valid", "duplicate", "absent", "conflict", "wrong-phase", "foreign"]
)
def test_retry_parent_provenance_is_not_inferred_from_inheritance_claim(
    retry: str,
    parent_kind: str,
    tmp_path: Path,
) -> None:
    contract = replace(CONTRACT, retry=retry)
    scope = Scope("request", 1)
    selected = replace(
        SELECT,
        scope=scope,
        selection="retry-token",
        phase=retry,
        parent_scope=SCOPE,
        parent_selection="token",
    )
    parents = {
        "valid": (SELECT,),
        "duplicate": (SELECT, replace(SELECT, identifier="second")),
        "absent": (),
        "conflict": (SELECT, replace(SELECT, identifier="second", digest=B)),
        "wrong-phase": (replace(SELECT, phase="resolve_at_load"),),
        "foreign": (replace(SELECT, scope=Scope("foreign", 0)),),
    }[parent_kind]
    observation = Observation(
        contract,
        scope,
        (
            *parents,
            selected,
            replace(LOAD, scope=scope, selection="retry-token"),
            replace(CLOSE, scope=scope),
        ),
    )
    candidate = receipt_checker(observation)
    result = baseline.provenance_checker(observation, tmp_path / "signed")
    assert (result.decision.verdict, result.decision.eligibility) == (
        candidate.verdict,
        candidate.eligibility,
    )


@pytest.mark.parametrize(
    ("expected", "observed", "invalid", "missing", "result"),
    [
        ({"model.pkl": A}, {"model.pkl": A}, False, False, "pass"),
        ({"model.pkl": A}, {"model.pkl": B}, False, False, "artifact_rule_mismatch"),
        ({"model.pkl": A}, {}, False, False, "artifact_rule_mismatch"),
        (
            {"model.pkl": A},
            {"model.pkl": A, "extra.pkl": B},
            False,
            False,
            "artifact_rule_mismatch",
        ),
        ({"model.pkl": A}, {"model.pkl": A}, True, False, "inadmissible_signature"),
        ({"model.pkl": A}, {"model.pkl": A}, False, True, "missing_link"),
    ],
    ids=["match", "wrong", "require", "disallow", "signature", "link"],
)
def test_actual_in_toto_full_verification_enforces_rules_and_signatures(
    expected: dict[str, str],
    observed: dict[str, str],
    invalid: bool,
    missing: bool,
    result: str,
    tmp_path: Path,
) -> None:
    assert (
        baseline.verify_chain(
            expected, observed, tmp_path / "links", invalid_signature=invalid, missing_link=missing
        )
        == result
    )
    assert not list(tmp_path.rglob("*.pem"))
    assert not list(tmp_path.rglob("*.key"))


def test_missing_policy_and_inadmissible_signature_are_not_violation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = baseline.provenance_checker(
        Observation(None, SCOPE, (SELECT, LOAD, CLOSE)), tmp_path / "unused"
    )
    assert result.decision.verdict == "unknown" and result.verifier_calls == 0
    monkeypatch.setattr(baseline, "verify_chain", lambda *args: "inadmissible_signature")
    result = baseline.provenance_checker(
        Observation(CONTRACT, SCOPE, (SELECT, LOAD, CLOSE)), tmp_path / "unused"
    )
    assert result.decision.verdict == "unknown" and result.artifact_rules_passed is None


def test_pinned_optional_versions_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(baseline, "version", lambda name: baseline.DEPENDENCIES[name])
    assert baseline.dependency_versions() == baseline.DEPENDENCIES
    monkeypatch.setattr(baseline, "version", lambda name: "0.0.0")
    with pytest.raises(RuntimeError, match="supported version"):
        baseline.dependency_versions()

    def missing(name: str) -> str:
        raise baseline.PackageNotFoundError(name)

    monkeypatch.setattr(baseline, "version", missing)
    with pytest.raises(RuntimeError, match="optional dependencies"):
        baseline.dependency_versions()


def _independent_worlds(
    selected: str | None,
    phase: str,
    witnessed: tuple[str, ...],
    closure: int | None,
) -> tuple[str | None, str]:
    # Independent observation relation and predicate: no shared admission,
    # candidate checker, or compatible-completions implementation is called.
    worlds = []
    for required, count in product(
        (selected,) if selected else (A, B), (closure,) if closure is not None else (0, 1, 2)
    ):
        for actual in product((A, B), repeat=count):
            if Counter(witnessed) <= Counter(actual):
                violation = (
                    count > 1
                    or any(d != required for d in actual)
                    or (count > 0 and selected is not None and phase != "pin_at_acceptance")
                )
                worlds.append((count, violation))
    if not worlds:
        return "conflict", "load"
    if all(count == 0 for count, _ in worlds):
        return None, "no_new_load"
    eligibility = "load" if all(count > 0 for count, _ in worlds) else "undetermined"
    values = {violation for _, violation in worlds}
    return (
        "unknown" if len(values) != 1 else "violation" if True in values else "compliant"
    ), eligibility


def test_signed_adapter_against_independently_enumerated_144_frames(tmp_path: Path) -> None:
    frames = product(
        (None, A, B),
        ("pin_at_acceptance", "resolve_at_load"),
        ((), (A,), (B,), (A, A), (A, B), (B, B)),
        (None, 0, 1, 2),
    )
    checked = 0
    for index, (selected, phase, loads, closure) in enumerate(frames):
        records = [replace(SELECT, digest=selected, phase=phase)] if selected else []
        records.extend(replace(LOAD, identifier=f"load-{i}", digest=d) for i, d in enumerate(loads))
        if closure is not None:
            records.append(replace(CLOSE, load_count=closure))
        observation = Observation(CONTRACT, SCOPE, tuple(records))
        decision = baseline.provenance_checker(observation, tmp_path / str(index)).decision
        assert (decision.verdict, decision.eligibility) == _independent_worlds(
            selected, phase, loads, closure
        )
        checked += 1
    assert checked == 144
