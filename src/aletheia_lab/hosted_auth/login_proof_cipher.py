"""Authenticated encryption primitive for a future login-intent repository.

Key provisioning, rotation, access control and database lifecycle are outside
this component. It does not persist a transaction or enable authentication.
"""

from __future__ import annotations

import json
import re
import secrets

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import SecretBytes, SecretStr

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_PROOF = re.compile(r"^[A-Za-z0-9._~-]{43,128}$")


class ProofRejected(ValueError):
    def __init__(self) -> None:
        super().__init__("Login transaction proof is invalid.")


def _associated(intent_id: str, browser_digest: str, state_digest: str) -> bytes:
    if (not isinstance(intent_id, str) or not _ID.fullmatch(intent_id)
            or not isinstance(browser_digest, str) or not _DIGEST.fullmatch(browser_digest)
            or not isinstance(state_digest, str) or not _DIGEST.fullmatch(state_digest)):
        raise ProofRejected()
    return ("aletheia-login-proof-v1\0" + intent_id + "\0" + browser_digest + "\0" + state_digest).encode("ascii")


class LoginProofCipher:
    """AES-256-GCM binds ciphertext to an intent, browser and state digest."""

    def __init__(self, key: SecretBytes) -> None:
        if not isinstance(key, SecretBytes) or len(key.get_secret_value()) != 32:
            raise ValueError("32-byte encryption key required")
        self._cipher = AESGCM(key.get_secret_value())

    def seal(self, *, intent_id: str, browser_digest: str, state_digest: str,
             nonce: SecretStr, code_verifier: SecretStr) -> bytes:
        aad = _associated(intent_id, browser_digest, state_digest)
        if not isinstance(nonce, SecretStr) or not isinstance(code_verifier, SecretStr):
            raise ProofRejected()
        values = (nonce.get_secret_value(), code_verifier.get_secret_value())
        if not all(_PROOF.fullmatch(value) for value in values):
            raise ProofRejected()
        payload = json.dumps(values, separators=(",", ":")).encode("ascii")
        iv = secrets.token_bytes(12)
        return b"\x01" + iv + self._cipher.encrypt(iv, payload, aad)

    def open(self, *, intent_id: str, browser_digest: str, state_digest: str,
             ciphertext: bytes) -> tuple[SecretStr, SecretStr]:
        aad = _associated(intent_id, browser_digest, state_digest)
        if not isinstance(ciphertext, bytes) or not 30 <= len(ciphertext) <= 512 or ciphertext[0] != 1:
            raise ProofRejected()
        try:
            payload = self._cipher.decrypt(ciphertext[1:13], ciphertext[13:], aad)
            values = json.loads(payload)
            if (not isinstance(values, list) or len(values) != 2
                    or not all(isinstance(value, str) and _PROOF.fullmatch(value) for value in values)):
                raise ProofRejected()
            return SecretStr(values[0]), SecretStr(values[1])
        except (InvalidTag, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            raise ProofRejected() from None
