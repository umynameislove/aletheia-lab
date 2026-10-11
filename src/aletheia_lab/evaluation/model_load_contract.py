"""Bounded, trusted model-load evidence contracts; not whole-inference attestation."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from itertools import product
from typing import Literal

Verdict = Literal["compliant", "violation", "unknown", "conflict"]
Policy = Literal["pin_at_acceptance", "resolve_at_load"]
Kind = Literal["selection", "load", "closure", "cache_hit", "registry"]
Eligibility = Literal["load", "no_new_load", "undetermined"]


def _digest(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


@dataclass(frozen=True)
class Scope:
    request: str
    attempt: int

    def __post_init__(self) -> None:
        if not self.request or len(self.request) > 100 or type(self.attempt) is not int:
            raise ValueError("invalid request/attempt scope")
        if self.attempt < 0:
            raise ValueError("negative attempt")


@dataclass(frozen=True)
class LoadContract:
    """Exact-byte policy for one completed attempt with at most one intended load.

    Operational completions include wrong artifacts and an extra unobserved load;
    the normative contract does not remove these violating histories. Cache hits
    without deserialization are reported separately, not scored as successful loads.
    Retry ``reselect`` resolves at that retry's load boundary, not enqueue time.
    ``inherit`` binds the authoritative root selection; intermediate inheritance
    chains are outside this bounded model and cannot warrant compliance.
    """

    policy: Policy
    artifact_domain: tuple[str, ...]
    retry: Literal["inherit", "reselect"] = "inherit"
    cache: Literal["reuse", "reload"] = "reuse"

    def __post_init__(self) -> None:
        if self.policy not in {"pin_at_acceptance", "resolve_at_load"}:
            raise ValueError("unknown selection boundary")
        if self.retry not in {"inherit", "reselect"} or self.cache not in {"reuse", "reload"}:
            raise ValueError("unknown lifecycle policy")
        if not 2 <= len(self.artifact_domain) <= 4:
            raise ValueError("bounded operational domain requires two to four artifacts")
        if len(set(self.artifact_domain)) != len(self.artifact_domain):
            raise ValueError("duplicate operational artifact")
        if not all(_digest(value) for value in self.artifact_domain):
            raise ValueError("invalid artifact digest")


@dataclass(frozen=True)
class Record:
    """Caller-admitted trusted observer record, not self-authenticating JSON.

    Selection is the actual returned registry snapshot. Load is the exact buffer
    passed to the deserializer, not the manifest or a later file read. Scope and
    selection token are part of admission, even for inherited retries.
    """

    identifier: str
    scope: Scope
    kind: Kind
    digest: str | None = None
    selection: str | None = None
    revision: int | None = None
    phase: str | None = None
    load_count: int | None = None
    parent_scope: Scope | None = None
    parent_selection: str | None = None

    def __post_init__(self) -> None:
        if not self.identifier or self.kind not in {
            "selection",
            "load",
            "closure",
            "cache_hit",
            "registry",
        }:
            raise ValueError("invalid record identity/kind")
        if self.digest is not None and not _digest(self.digest):
            raise ValueError("invalid observed digest")
        for value in (self.revision, self.load_count):
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError("invalid revision/count")
        if self.kind in {"selection", "load"} and (self.digest is None or not self.selection):
            raise ValueError("binding record lacks digest or selection token")
        if self.kind == "selection" and (self.revision is None or not self.phase):
            raise ValueError("selection lacks snapshot revision/boundary")
        if self.kind == "closure" and self.load_count is None:
            raise ValueError("closure lacks load count")


@dataclass(frozen=True)
class Observation:
    contract: LoadContract | None
    scope: Scope
    records: tuple[Record, ...]


@dataclass(frozen=True)
class Decision:
    verdict: Verdict | None
    reason: str
    eligibility: Eligibility = "load"
    compatible_count: int | None = None


@dataclass(frozen=True)
class Frame:
    """Shared admission only; baseline and completion monitor decide independently."""

    expected: str | None
    observed: tuple[str, ...]
    closed_count: int | None
    cache_hit: bool
    problem: str | None
    rule_violation: bool = False


def _one_value(values: Collection[object]) -> bool:
    return len(values) <= 1


def admit(observation: Observation) -> Frame:
    """Ignore foreign attempts/native declarations; reject contradictory bindings."""
    records: dict[str, Record] = {}
    for record in observation.records:
        if record.scope != observation.scope:
            continue
        if record.identifier in records and records[record.identifier] != record:
            return Frame(None, (), None, False, "conflicting_record_identity")
        records[record.identifier] = record
    selections = [record for record in records.values() if record.kind == "selection"]
    loads = [record for record in records.values() if record.kind == "load"]
    closures = {record.load_count for record in records.values() if record.kind == "closure"}
    cache_hit = any(record.kind == "cache_hit" for record in records.values())
    selection_values = {
        (
            record.digest,
            record.selection,
            record.revision,
            record.phase,
            record.parent_scope,
            record.parent_selection,
        )
        for record in selections
    }
    if not _one_value(selection_values) or not _one_value(set(closures)):
        return Frame(None, (), None, cache_hit, "conflicting_receipts")
    expected = selections[0].digest if selections else None
    count = next(iter(closures), None)
    observed = tuple(record.digest for record in loads if record.digest is not None)
    frame = Frame(expected, observed, count, cache_hit, None)
    frame = _validate_frame(observation, frame, selections, loads)
    return _selection_rules(observation, frame, selections)


def _validate_frame(
    observation: Observation, frame: Frame, selections: list[Record], loads: list[Record]
) -> Frame:
    problem: str | None = None
    contract = observation.contract
    if len(loads) > 2 or (frame.closed_count is not None and frame.closed_count > 2):
        problem = "outside_bounded_load_model"
    elif frame.closed_count is not None and frame.closed_count < len(loads):
        problem = "closure_contradicts_observed_loads"
    elif contract is not None:
        problem = _contract_problem(observation, selections, loads, frame)
    return Frame(frame.expected, frame.observed, frame.closed_count, frame.cache_hit, problem)


def _contract_problem(
    observation: Observation, selections: list[Record], loads: list[Record], frame: Frame
) -> str | None:
    contract = observation.contract
    if contract is None:
        raise ValueError("contract validation requires a declared policy")
    if any(
        value not in contract.artifact_domain
        for value in (*frame.observed, frame.expected)
        if value is not None
    ):
        return "artifact_outside_operational_domain"
    if selections and any(record.selection != selections[0].selection for record in loads):
        return "selection_token_misjoin"
    return None


def _selection_rules(observation: Observation, frame: Frame, selections: list[Record]) -> Frame:
    contract = observation.contract
    if frame.problem or contract is None or not selections:
        return frame
    selected = selections[0]
    phase = contract.retry if observation.scope.attempt else contract.policy
    violation = selected.phase != phase
    expected = frame.expected
    problem = None
    if observation.scope.attempt and contract.retry == "inherit":
        expected, inherited_violation, problem = _parent_binding(observation, selected)
        violation = violation or inherited_violation
    if expected is not None and expected not in contract.artifact_domain:
        problem = "parent_artifact_outside_operational_domain"
    return Frame(expected, frame.observed, frame.closed_count, frame.cache_hit, problem, violation)


def _parent_binding(
    observation: Observation, selected: Record
) -> tuple[str | None, bool, str | None]:
    """An inheritance claim alone cannot authenticate the parent's accepted pin."""
    origin = selected.parent_scope
    if origin is None or origin.request != observation.scope.request:
        return None, False, None
    if origin.attempt != 0 or not selected.parent_selection:
        return None, False, None
    parents = [
        record
        for record in observation.records
        if record.scope == origin
        and record.kind == "selection"
        and record.selection == selected.parent_selection
    ]
    if not parents:
        return None, False, None
    parent_values = {(record.digest, record.revision, record.phase) for record in parents}
    if len(parent_values) != 1:
        return None, False, "conflicting_parent_receipts"
    parent = parents[0]
    contract = observation.contract
    if contract is None or parent.phase != contract.policy:
        return None, False, None
    return parent.digest, selected.digest != parent.digest, None


def receipt_checker(observation: Observation) -> Decision:
    """Strong simple S: scoped selection, actual buffer, lifecycle and closure.

    No hidden reference, present alias lookup or prevention guard is consulted.
    A witnessed violation is conclusive before closure; compliance is not.
    """
    frame = admit(observation)
    if frame.problem:
        return Decision("conflict", frame.problem)
    if observation.contract is None:
        return Decision("unknown", "missing_policy", "undetermined")
    if frame.closed_count == 0:
        return Decision(None, "closed_without_new_load", "no_new_load")
    if frame.rule_violation and (frame.observed or frame.closed_count is not None):
        return Decision("violation", "observed_selection_rule_violation")
    if len(frame.observed) > 1 or frame.closed_count == 2:
        return Decision("violation", "extra_load_violates_single_load_contract")
    if frame.expected is not None and any(value != frame.expected for value in frame.observed):
        return Decision("violation", "observed_wrong_buffer")
    if frame.expected is None:
        eligibility: Eligibility = (
            "load" if frame.observed or frame.closed_count is not None else "undetermined"
        )
        return Decision("unknown", "missing_selection", eligibility)
    if not frame.observed:
        return Decision(
            "unknown", "missing_buffer_witness", "load" if frame.closed_count else "undetermined"
        )
    if frame.closed_count is None:
        return Decision("unknown", "missing_closure")
    return Decision("compliant", "scoped_buffer_matches_closed_selection")


@dataclass(frozen=True)
class Completion:
    selected: str
    loads: tuple[str, ...]
    selection_rule_violation: bool = False

    @property
    def violation(self) -> bool:
        return (
            bool(self.loads)
            and self.selection_rule_violation
            or len(self.loads) > 1
            or any(digest != self.selected for digest in self.loads)
        )


def compatible_completions(observation: Observation) -> tuple[Completion, ...]:
    """Exact declared finite Γ, including extra/wrong loads; not arbitrary traces.

    Without closure, up to two loads remain possible. Record delivery order is
    not execution order: the predicate is permutation invariant. This does not
    implement a general temporal logic engine or infer registry linearizability.
    """
    frame = admit(observation)
    contract = observation.contract
    if frame.problem or contract is None:
        return ()
    selected = (frame.expected,) if frame.expected is not None else contract.artifact_domain
    counts = (frame.closed_count,) if frame.closed_count is not None else (0, 1, 2)
    worlds = []
    for expected, count in product(selected, counts):
        for loads in product(contract.artifact_domain, repeat=count):
            remaining = list(loads)
            for digest in frame.observed:
                if digest not in remaining:
                    break
                remaining.remove(digest)
            else:
                worlds.append(Completion(expected, loads, frame.rule_violation))
    return tuple(worlds)


def completion_monitor(observation: Observation) -> Decision:
    """Bounded T reference on exactly S's admitted evidence; no oracle truth."""
    frame = admit(observation)
    if frame.problem:
        return Decision("conflict", frame.problem, compatible_count=0)
    if observation.contract is None:
        return Decision("unknown", "missing_policy", "undetermined")
    worlds = compatible_completions(observation)
    if not worlds:
        return Decision("conflict", "no_compatible_completion", compatible_count=0)
    if all(not world.loads for world in worlds):
        return Decision(None, "closed_without_new_load", "no_new_load", len(worlds))
    statuses = {world.violation for world in worlds}
    verdict: Verdict = (
        "unknown" if len(statuses) > 1 else "violation" if True in statuses else "compliant"
    )
    eligibility: Eligibility = "load" if all(world.loads for world in worlds) else "undetermined"
    return Decision(verdict, "finite_completion_unanimity", eligibility, len(worlds))
