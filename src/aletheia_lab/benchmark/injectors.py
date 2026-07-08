"""Fault injection interfaces.

Concrete injectors should be deterministic and should emit both:

1. a hidden ground-truth record; and
2. a public evidence bundle that does not leak the answer key.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from aletheia_lab.benchmark.manifest import BenchmarkCase


@dataclass(frozen=True)
class InjectionResult:
    """Output of a fault injector."""

    case: BenchmarkCase
    artifact_paths: list[str]


class FaultInjector(Protocol):
    """Protocol for deterministic fault injectors."""

    fault_type: str

    def inject(self, seed: int) -> InjectionResult:
        """Create one benchmark case for the provided seed."""
        ...
