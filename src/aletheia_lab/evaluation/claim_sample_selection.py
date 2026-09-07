"""Deterministic, duplicate-safe selection for human claim validation."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from typing import Protocol, TypeVar


class SamplingEntry(Protocol):
    @property
    def automatic_label(self) -> str: ...

    @property
    def claim_type(self) -> str: ...

    @property
    def evidence_condition(self) -> str: ...

    @property
    def variant(self) -> str: ...

    @property
    def entry_sha256(self) -> str: ...

    @property
    def case_family_id(self) -> str: ...

    @property
    def output_id(self) -> str: ...

    @property
    def claim_text(self) -> str: ...

    @property
    def claim_id(self) -> str: ...

    @property
    def source_partition(self) -> str: ...


class SamplingPolicy(Protocol):
    @property
    def protocol_sha256(self) -> str: ...

    @property
    def sample_target(self) -> int: ...

    @property
    def automatic_label_quota(self) -> int: ...

    @property
    def maximum_claims_per_family_per_label(self) -> int: ...

    @property
    def maximum_claims_per_output_per_label(self) -> int: ...

    @property
    def source_partition(self) -> str: ...


EntryT = TypeVar("EntryT", bound=SamplingEntry)
Rank = Callable[[str, str, object], str]
Fail = Callable[[str], None]


def _select_label_stratum(
    entries: Sequence[EntryT],
    *,
    label: str,
    protocol: SamplingPolicy,
    selected_claim_texts: set[str],
    rank: Rank,
    fail: Fail,
) -> tuple[EntryT, ...]:
    strata: dict[tuple[str, str, str], list[EntryT]] = defaultdict(list)
    for entry in entries:
        if entry.automatic_label == label:
            strata[(entry.claim_type, entry.evidence_condition, entry.variant)].append(entry)
    for key, values in strata.items():
        values.sort(key=lambda item: rank(protocol.protocol_sha256, "entry", item.entry_sha256))
        strata[key] = values
    ordered_keys = sorted(
        strata,
        key=lambda key: rank(protocol.protocol_sha256, "stratum", key),
    )
    selected: list[EntryT] = []
    family_counts: Counter[str] = Counter()
    output_counts: Counter[str] = Counter()
    while len(selected) < protocol.automatic_label_quota:
        progress = False
        for key in ordered_keys:
            candidates = strata[key]
            while candidates:
                candidate = candidates.pop(0)
                if (
                    family_counts[candidate.case_family_id]
                    >= protocol.maximum_claims_per_family_per_label
                    or output_counts[candidate.output_id]
                    >= protocol.maximum_claims_per_output_per_label
                    or candidate.claim_text in selected_claim_texts
                ):
                    continue
                selected.append(candidate)
                selected_claim_texts.add(candidate.claim_text)
                family_counts[candidate.case_family_id] += 1
                output_counts[candidate.output_id] += 1
                progress = True
                break
            if len(selected) == protocol.automatic_label_quota:
                break
        if not progress:
            fail(f"insufficient eligible development claims for automatic label {label}")
    return tuple(selected)


def select_balanced_validation_sample(
    entries: Sequence[EntryT],
    protocol: SamplingPolicy,
    *,
    labels: Sequence[str],
    rank: Rank,
    fail: Fail,
) -> tuple[EntryT, ...]:
    """Apply frozen quotas and caps without identity or text duplication."""

    if len(entries) < protocol.sample_target:
        fail("development claim pool is smaller than the frozen target")
    if any(entry.source_partition != protocol.source_partition for entry in entries):
        fail("only development-partition claims may enter instrument validation")
    claim_ids = [entry.claim_id for entry in entries]
    entry_hashes = [entry.entry_sha256 for entry in entries]
    if len(claim_ids) != len(set(claim_ids)) or len(entry_hashes) != len(set(entry_hashes)):
        fail("claim pool must not contain duplicate identities")
    selected_texts: set[str] = set()
    selected = tuple(
        entry
        for label in labels
        for entry in _select_label_stratum(
            entries,
            label=label,
            protocol=protocol,
            selected_claim_texts=selected_texts,
            rank=rank,
            fail=fail,
        )
    )
    if len(selected_texts) != protocol.sample_target:
        fail("selected validation sample contains duplicate canonical claim text")
    return tuple(
        sorted(
            selected,
            key=lambda item: rank(protocol.protocol_sha256, "blind-order", item.entry_sha256),
        )
    )


__all__ = ["select_balanced_validation_sample"]
