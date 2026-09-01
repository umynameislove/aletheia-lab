"""Stable public API for outcome-blind diagnosis development validation.

Implementation lives in :mod:`aletheia_lab.diagnosis._development`. This
facade preserves existing imports while keeping internal dependencies explicit.
"""

from aletheia_lab.diagnosis._development.contracts import (
    DEVELOPMENT_MODE,
    DevelopmentCase,
    DevelopmentEvidenceItem,
    DevelopmentFailureReceipt,
    DevelopmentPilotError,
    DevelopmentPilotManifest,
    DevelopmentPilotPlan,
    DevelopmentResourceObservation,
    DevelopmentRunRecord,
    DevelopmentTerminalReceipt,
    DevelopmentToolEvent,
    DevelopmentToolLedger,
    DevelopmentVariantRequest,
    DevelopmentVariantResponse,
)
from aletheia_lab.diagnosis._development.executor import (
    DeterministicDevelopmentExecutor,
    DevelopmentVariantExecutor,
)
from aletheia_lab.diagnosis._development.planning import (
    build_development_case,
    build_development_evidence_item,
    build_development_plan,
    load_development_plan,
)
from aletheia_lab.diagnosis._development.resources import (
    resource_observation_for_request,
)
from aletheia_lab.diagnosis._development.runner import run_development_pilot
from aletheia_lab.diagnosis._development.store import (
    DevelopmentArtifactStore,
    load_run_record,
    load_run_request,
    load_run_response,
)
from aletheia_lab.diagnosis._development.validation import (
    validate_request_against_authority,
    validate_response_against_authority,
)

__all__ = [
    "DEVELOPMENT_MODE",
    "DevelopmentArtifactStore",
    "DevelopmentCase",
    "DevelopmentEvidenceItem",
    "DevelopmentFailureReceipt",
    "DevelopmentPilotError",
    "DevelopmentPilotManifest",
    "DevelopmentPilotPlan",
    "DevelopmentResourceObservation",
    "DevelopmentRunRecord",
    "DevelopmentTerminalReceipt",
    "DevelopmentToolEvent",
    "DevelopmentToolLedger",
    "DevelopmentVariantExecutor",
    "DevelopmentVariantRequest",
    "DevelopmentVariantResponse",
    "DeterministicDevelopmentExecutor",
    "build_development_case",
    "build_development_evidence_item",
    "build_development_plan",
    "load_development_plan",
    "load_run_record",
    "load_run_request",
    "load_run_response",
    "run_development_pilot",
    "resource_observation_for_request",
    "validate_request_against_authority",
    "validate_response_against_authority",
]
