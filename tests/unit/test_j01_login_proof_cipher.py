"""Proof encryption detects changes to either ciphertext or its binding."""

import pytest
from pydantic import SecretBytes, SecretStr

from aletheia_lab.hosted_auth.login_proof_cipher import LoginProofCipher, ProofRejected


def test_encrypt_round_trip_is_random_and_does_not_contain_plaintext():
    cipher = LoginProofCipher(SecretBytes(b"k" * 32))
    binding = {"intent_id": "intent-1", "browser_digest": "a" * 64, "state_digest": "b" * 64}
    first = cipher.seal(**binding, nonce=SecretStr("n" * 43), code_verifier=SecretStr("v" * 43))
    second = cipher.seal(**binding, nonce=SecretStr("n" * 43), code_verifier=SecretStr("v" * 43))
    assert first != second and b"n" * 43 not in first and b"v" * 43 not in first
    nonce, verifier = cipher.open(**binding, ciphertext=first)
    assert nonce.get_secret_value() == "n" * 43 and verifier.get_secret_value() == "v" * 43


@pytest.mark.parametrize("change", ["ciphertext", "browser_digest", "state_digest", "intent_id", "key"])
def test_tampering_or_wrong_key_is_rejected(change):
    cipher = LoginProofCipher(SecretBytes(b"k" * 32))
    binding = {"intent_id": "intent-1", "browser_digest": "a" * 64, "state_digest": "b" * 64}
    payload = cipher.seal(**binding, nonce=SecretStr("n" * 43), code_verifier=SecretStr("v" * 43))
    if change == "ciphertext":
        payload = payload[:-1] + bytes([payload[-1] ^ 1])
    elif change == "key":
        cipher = LoginProofCipher(SecretBytes(b"q" * 32))
    else:
        binding[change] = {"browser_digest": "c" * 64, "state_digest": "d" * 64,
                           "intent_id": "intent-2"}[change]
    with pytest.raises(ProofRejected, match="Login transaction proof is invalid"):
        cipher.open(**binding, ciphertext=payload)


def test_rejects_invalid_key_and_proof():
    with pytest.raises(ValueError):
        LoginProofCipher(SecretBytes(b"short"))
    with pytest.raises(ProofRejected):
        LoginProofCipher(SecretBytes(b"k" * 32)).seal(
            intent_id="i", browser_digest="a" * 64, state_digest="b" * 64,
            nonce=SecretStr("too-short"), code_verifier=SecretStr("v" * 43),
        )
