"""Diagnosis variants, strict request contracts and matched-pilot execution."""

from aletheia_lab.diagnosis.runner import run_p1_matched_pilot, validate_p1_matched_pilot
from aletheia_lab.diagnosis.variant_registry import (
    DiagnosisVariantRegistry,
    DiagnosisVariantRequestBinding,
    VariantRegistryError,
    bind_variant_request,
    build_variant_registry,
    load_variant_registry,
    validate_variant_request_binding,
)

__all__ = [
    "DiagnosisVariantRegistry",
    "DiagnosisVariantRequestBinding",
    "VariantRegistryError",
    "bind_variant_request",
    "build_variant_registry",
    "load_variant_registry",
    "validate_variant_request_binding",
    "run_p1_matched_pilot",
    "validate_p1_matched_pilot",
]
