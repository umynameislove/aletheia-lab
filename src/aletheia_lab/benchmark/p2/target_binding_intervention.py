"""Target-only row binding faults, independent of score-column intervention."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from aletheia_lab.benchmark.p2.score_column_mapping import validate_score_record_ids
from aletheia_lab.evidence.schema import sha256_text

TARGET_SELECTOR_SEED = "target-binding-development-v1"
TARGET_SHARD_COUNT = 20
TargetDose = Literal[0, 1, 2, 4]


class TargetBindingError(ValueError):
    """A source or row-target intervention is malformed."""


@dataclass(frozen=True, slots=True)
class TargetBindingSource:
    dataset_id: str
    target_rows: tuple[tuple[str, int], ...]

    def __post_init__(self) -> None:
        validate_score_record_ids((self.dataset_id,))
        raw_rows = tuple(self.target_rows)
        if any(not isinstance(row, (tuple, list)) or len(row) != 2 for row in raw_rows):
            raise TargetBindingError("targets require row-ID and binary-label pairs")
        rows = tuple((row[0], row[1]) for row in raw_rows)
        validate_score_record_ids(tuple(row[0] for row in rows))
        if any(type(row[1]) is not int or row[1] not in (0, 1) for row in rows):
            raise TargetBindingError("source target labels must be binary integers")
        if {row[1] for row in rows} != {0, 1}:
            raise TargetBindingError("the development source must contain both classes")
        object.__setattr__(self, "target_rows", rows)

    @property
    def record_ids(self) -> tuple[str, ...]:
        return tuple(row_id for row_id, _ in self.target_rows)


@dataclass(frozen=True, slots=True)
class TargetBindingIntervention:
    """Donor lineage is evaluator-only, not a diagnosis-visible cause label."""

    selected_shard_count: int
    row_shards: tuple[int, ...]
    donor_record_ids: tuple[str, ...]
    observed_target_rows: tuple[tuple[str, int], ...]
    selected_record_ids: tuple[str, ...]
    changed_record_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PairedTargetBindingIntervention:
    """Adversarial donor swaps; the external pair search is development-only."""

    swapped_pairs: tuple[tuple[str, str], ...]
    donor_record_ids: tuple[str, ...]
    observed_target_rows: tuple[tuple[str, int], ...]
    changed_record_ids: tuple[str, ...]


def apply_paired_target_binding_fault(
    source: TargetBindingSource, *, swapped_pairs: tuple[tuple[str, str], ...]
) -> PairedTargetBindingIntervention:
    """Misjoin opposite-label row targets without changing any score column.

    This function does not search for pairs or consult model scores. A caller
    may pass pairs chosen adversarially on development data, but that selection
    is a separate, explicitly disclosed step.
    """

    if not isinstance(source, TargetBindingSource) or not isinstance(swapped_pairs, tuple):
        raise TargetBindingError("typed target source and pair ledger are required")
    labels = dict(source.target_rows)
    donors = {row_id: row_id for row_id in source.record_ids}
    used: set[str] = set()
    for pair in swapped_pairs:
        if (
            not isinstance(pair, tuple)
            or len(pair) != 2
            or not all(isinstance(row_id, str) for row_id in pair)
            or pair[0] not in labels
            or pair[1] not in labels
            or pair[0] in used
            or pair[1] in used
            or labels[pair[0]] != 0
            or labels[pair[1]] != 1
        ):
            raise TargetBindingError("pair ledger must contain disjoint 0/1 source rows")
        zero_id, one_id = pair
        donors[zero_id] = one_id
        donors[one_id] = zero_id
        used.update(pair)
    donor_ids = tuple(donors[row_id] for row_id in source.record_ids)
    observed = tuple(
        (row_id, labels[donor]) for row_id, donor in zip(source.record_ids, donor_ids, strict=True)
    )
    return PairedTargetBindingIntervention(
        swapped_pairs,
        donor_ids,
        observed,
        tuple(row_id for row_id in source.record_ids if row_id in used),
    )


def apply_target_binding_fault(
    source: TargetBindingSource, *, selected_shard_count: TargetDose
) -> TargetBindingIntervention:
    """Cyclically misjoin selected row IDs without consulting labels or scores.

    The selected IDs are sorted by an independent hash and shifted by one.
    Same-label donors are retained, not filtered to manufacture an effect.
    A permutation preserves global class counts; flat effects remain controls.
    """

    if not isinstance(source, TargetBindingSource):
        raise TargetBindingError("a typed pre-intervention target source is required")
    if type(selected_shard_count) is not int or selected_shard_count not in (0, 1, 2, 4):
        raise TargetBindingError("dose must be zero, one, two or four development shards")
    ids = source.record_ids
    keys = tuple(sha256_text(f"{TARGET_SELECTOR_SEED}\x00{source.dataset_id}\x00{i}") for i in ids)
    shards = tuple(int(key, 16) % TARGET_SHARD_COUNT for key in keys)
    selected = sorted(
        (i for i, shard in enumerate(shards) if shard < selected_shard_count),
        key=lambda i: (keys[i], ids[i]),
    )
    donors = list(ids)
    for position, index in enumerate(selected):
        donors[index] = ids[selected[(position + 1) % len(selected)]]
    by_id = dict(source.target_rows)
    rows = tuple((row_id, by_id[donor]) for row_id, donor in zip(ids, donors, strict=True))
    changed = tuple(
        row_id
        for (row_id, before), (_, after) in zip(source.target_rows, rows, strict=True)
        if before != after
    )
    selected_ids = tuple(
        row_id for row_id, shard in zip(ids, shards, strict=True) if shard < selected_shard_count
    )
    return TargetBindingIntervention(
        selected_shard_count, shards, tuple(donors), rows, selected_ids, changed
    )


def restore_target_bindings(
    source: TargetBindingSource,
    intervention: TargetBindingIntervention | PairedTargetBindingIntervention,
) -> tuple[tuple[str, int], ...]:
    """Restore labels by the pre-intervention source join, not by reversing scores."""

    if not isinstance(source, TargetBindingSource) or not isinstance(
        intervention, (TargetBindingIntervention, PairedTargetBindingIntervention)
    ):
        raise TargetBindingError("typed target source and intervention are required")
    if tuple(row_id for row_id, _ in intervention.observed_target_rows) != source.record_ids:
        raise TargetBindingError("intervention rows differ from the source identity/order")
    source_labels = dict(source.target_rows)
    return tuple((row_id, source_labels[row_id]) for row_id in source.record_ids)
