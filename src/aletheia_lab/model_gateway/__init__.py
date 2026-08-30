"""Provider-neutral evaluation adapter contracts and deterministic fake runtime."""

from aletheia_lab.model_gateway.contracts import (
    AdapterInvocationError,
    AttemptRecord,
    AttemptTiming,
    CancellationProbe,
    Clock,
    GatewayContractError,
    GatewayExecutionResult,
    GatewayRequest,
    ParsedResponseArtifact,
    ProviderAdapter,
    ProviderBinding,
    ProviderCall,
    ProviderEnvelope,
    RawResponseArtifact,
    RuntimePolicyReference,
    UsageMetadata,
)
from aletheia_lab.model_gateway.fake import (
    DeterministicFakeAdapter,
    FakeFixture,
    FakeStep,
)
from aletheia_lab.model_gateway.runtime import (
    execute_gateway_request,
    prepare_gateway_request,
)

__all__ = [
    "AdapterInvocationError",
    "AttemptRecord",
    "AttemptTiming",
    "CancellationProbe",
    "Clock",
    "DeterministicFakeAdapter",
    "FakeFixture",
    "FakeStep",
    "GatewayContractError",
    "GatewayExecutionResult",
    "GatewayRequest",
    "ParsedResponseArtifact",
    "ProviderAdapter",
    "ProviderBinding",
    "ProviderCall",
    "ProviderEnvelope",
    "RawResponseArtifact",
    "RuntimePolicyReference",
    "UsageMetadata",
    "execute_gateway_request",
    "prepare_gateway_request",
]
