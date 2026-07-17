"""Compatibility import for the active P1 diagnosis variants."""

from aletheia_lab.diagnosis.schema import PilotVariant

DiagnosisVariant = PilotVariant

__all__ = ["DiagnosisVariant", "PilotVariant"]
