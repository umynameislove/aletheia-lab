"""Benchmark validation helpers."""

from __future__ import annotations

from collections.abc import Iterable

from aletheia_lab.benchmark.manifest import BenchmarkCase


def unique_case_ids(cases: Iterable[BenchmarkCase]) -> bool:
    """Return true when all case IDs are unique."""

    seen: set[str] = set()
    for case in cases:
        if case.case_id in seen:
            return False
        seen.add(case.case_id)
    return True


def has_required_fault_coverage(cases: Iterable[BenchmarkCase], minimum_per_fault: int) -> bool:
    """Check that each observed fault type has at least N cases."""

    counts: dict[str, int] = {}
    for case in cases:
        counts[case.fault_type] = counts.get(case.fault_type, 0) + 1
    return bool(counts) and all(count >= minimum_per_fault for count in counts.values())
