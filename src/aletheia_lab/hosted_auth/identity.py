"""Resolve trusted, library-validated OIDC identity to an existing local account.

No OIDC/token validation, provisioning, invitation, password or recovery service
is implemented here. No account/session is created by resolving a binding.
"""

from __future__ import annotations

import asyncio
import hmac
import math
from typing import Literal

from pydantic import ValidationError

from .repositories import (
    AccountBinding,
    IdentityDirectory,
    RepositoryDataRejected,
    ValidatedIdentity,
    repository_call,
)
from .settings import AuthPolicy

IdentityReason = Literal["unprovisioned", "disabled", "email_unverified", "issuer_mismatch"]


class IdentityRejected(RuntimeError):
    """Internal denial reasons; public error mapping requires J-01 review."""

    def __init__(self, reason: IdentityReason) -> None:
        self.reason = reason
        super().__init__("The authenticated identity cannot use this account.")


class AccountResolver:
    """Explicit issuer/subject lookup without email linking or role inference."""

    def __init__(
        self, directory: IdentityDirectory, policy: AuthPolicy, *,
        expected_issuer: str, timeout_seconds: float,
    ) -> None:
        if not expected_issuer:
            raise ValueError("configured issuer required")
        if isinstance(timeout_seconds, bool) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("positive finite identity deadline required")
        self._directory = directory
        self._policy = AuthPolicy.model_validate(policy.model_dump(warnings=False))
        self._issuer = expected_issuer
        self._timeout = timeout_seconds

    async def resolve(self, identity: ValidatedIdentity) -> AccountBinding:
        """Caller must use maintained OIDC verification before invoking this."""
        try:
            identity = ValidatedIdentity.model_validate(identity.model_dump(warnings=False))
        except (ValidationError, AttributeError, TypeError):
            raise RepositoryDataRejected() from None
        if identity.issuer != self._issuer:
            raise IdentityRejected("issuer_mismatch")
        if self._policy.require_verified_email and (not identity.email_verified or identity.email is None):
            raise IdentityRejected("email_unverified")
        async with asyncio.timeout(self._timeout):
            binding = await repository_call(self._directory.find_identity(
                issuer=identity.issuer, subject=identity.subject,
            ))
        if binding is None:
            raise IdentityRejected("unprovisioned")
        binding = self._check_binding(binding, identity)
        if not binding.enabled:
            raise IdentityRejected("disabled")
        if self._policy.require_verified_email and not binding.actor.email_verified:
            raise IdentityRejected("email_unverified")
        return binding

    @staticmethod
    def _check_binding(binding: AccountBinding, identity: ValidatedIdentity) -> AccountBinding:
        try:
            binding = AccountBinding.model_validate(binding.model_dump(warnings=False))
        except (ValidationError, AttributeError, TypeError):
            raise RepositoryDataRejected() from None
        if binding.issuer != identity.issuer or not hmac.compare_digest(
            binding.subject.get_secret_value().encode("utf-8"),
            identity.subject.get_secret_value().encode("utf-8"),
        ):
            raise RepositoryDataRejected()
        return binding
