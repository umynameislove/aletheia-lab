"""Bounded public import failures; worker exceptions never cross this boundary."""
from __future__ import annotations

from aletheia_lab.hosted_contract.imports import MappingFailure


class MappingInvalid(ValueError):
    def __init__(self, value: dict[str, object]) -> None:
        self.failure = MappingFailure.model_validate(value)
        super().__init__('The mapping contains invalid selections.')


class PreviewRequired(ValueError):
    def __init__(self) -> None:
        super().__init__('A current preview is required.')
