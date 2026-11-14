"""Pinned synthetic native seed geometry, never a capacity acceptance claim."""

from __future__ import annotations

import json
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aletheia_lab.project.identity import content_hasher

Size = Literal["S", "M", "L"]
ROLES = ("owner", "admin", "analyst", "reviewer", "viewer")


class SeedPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)
    schema_version: Literal["k17-native-seed-plan/v1"] = "k17-native-seed-plan/v1"
    namespace: str = Field(pattern=r"^k17_[a-z0-9]{8,20}$")
    seed: int = Field(default=17, ge=0, le=2**63 - 1)
    workspaces: int = Field(default=2, ge=2, le=2000)
    projects_per_workspace: int = Field(default=3, ge=1, le=5)
    generations: int = Field(default=2, ge=1, le=20)
    results_per_generation: int = Field(default=2, ge=1, le=10)
    size_cycle: tuple[Size, ...] = ("S", "M", "L")
    source_rows: dict[str, int] = Field(default_factory=lambda: {"S": 32, "M": 128, "L": 512})
    metric_series: dict[str, int] = Field(default_factory=lambda: {"S": 1, "M": 8, "L": 32})
    idle_seconds: int = Field(default=900, ge=60, le=86400)
    absolute_seconds: int = Field(default=28800, ge=60, le=604800)

    @model_validator(mode="after")
    def bounded(self) -> Self:
        if (
            not self.size_cycle
            or len(self.size_cycle) > 100
            or set(self.size_cycle) != {"S", "M", "L"}
        ):
            raise ValueError("All three size strata must be represented by the pinned cycle.")
        for values, maximum in ((self.source_rows, 100000), (self.metric_series, 32)):
            if set(values) != {"S", "M", "L"} or any(
                type(n) is not int or not 1 <= n <= maximum for n in values.values()
            ):
                raise ValueError("Bounded exact S/M/L dimensions are required.")
            if not values["S"] < values["M"] < values["L"]:
                raise ValueError("Physical source and metric strata must differ independently.")
        if self.idle_seconds > self.absolute_seconds:
            raise ValueError("Idle lifetime cannot exceed the real hard expiry.")
        if self.projects < 3 or {self.size(i) for i in range(self.projects)} != {"S", "M", "L"}:
            raise ValueError("The actual dataset must contain every physical stratum.")
        return self

    @property
    def projects(self) -> int:
        return self.workspaces * self.projects_per_workspace

    @property
    def principals(self) -> int:
        return self.projects * len(ROLES)

    @property
    def snapshots(self) -> int:
        return self.projects * self.generations

    @property
    def results(self) -> int:
        return self.snapshots * self.results_per_generation

    @property
    def total_source_rows(self) -> int:
        return self.generations * sum(self.source_rows[self.size(i)] for i in range(self.projects))

    def size(self, project: int) -> Size:
        return self.size_cycle[project % len(self.size_cycle)]

    def workspace(self, index: int) -> str:
        return f"ws_{self.namespace}_{index:04d}"

    def user(self, index: int) -> str:
        return f"a_{self.namespace}_{index:06d}"

    def fingerprint(self) -> str:
        return content_hasher(
            json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    def primary_gaps(self) -> tuple[str, ...]:
        gaps = []
        if (self.workspaces, self.projects, self.principals, self.snapshots, self.results) != (
            2000,
            10000,
            50000,
            200000,
            2000000,
        ):
            gaps.append("Primary catalogue cardinalities are not present.")
        if self.source_rows != {"S": 1000, "M": 10000, "L": 100000}:
            gaps.append("Primary source rows 1000/10000/100000 are not present.")
        claims = "/".join(str(2 * self.metric_series[size]) for size in ("S", "M", "L"))
        gaps.append(
            f"The native metric analyzer permits at most 64 series; actual fixture claims {claims} do not satisfy primary claims 100/1000/10000 or a 10000-node graph."
        )
        gaps.append(
            "A generated dataset does not qualify heavy job journeys, real IdP, deployment SLOs or production capacity."
        )
        return tuple(gaps)
