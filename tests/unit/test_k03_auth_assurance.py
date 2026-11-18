"""Explicit ACR policy with real signatures and mock HTTP, not MFA acceptance."""

import asyncio
from urllib.parse import parse_qs, urlsplit

import pytest
from joserfc.jwk import RSAKey
from pydantic import ValidationError
from test_k03_oidc_library import NOW, POLICY, _system

from aletheia_lab.hosted_auth.oidc import OidcRejected
from aletheia_lab.hosted_auth.settings import AuthPolicy, load_auth_policy

ACR = "urn:aletheia:local-test:mfa"


@pytest.fixture(scope="module")
def key():
    return RSAKey.generate_key(2048, {"kid": "synthetic-key"})


@pytest.mark.parametrize("value", ["", "two values", "x\n", "x\r", "x\t", "x\x00", "x" * 257, True, 2, []])
def test_required_acr_rejects_non_single_or_unbounded_configuration(value):
    with pytest.raises(ValidationError):
        AuthPolicy.model_validate({**POLICY.model_dump(), "required_acr": value})


def test_acr_policy_loads_without_creating_another_identity_config(tmp_path):
    path = tmp_path / "auth.toml"
    path.write_text('default_return_to = "/app"\nreturn_to_paths = ["/app"]\nrequired_acr = "' + ACR + '"\n',
                    encoding="utf-8")
    assert load_auth_policy(path).required_acr == ACR
    assert POLICY.required_acr is None


@pytest.mark.parametrize("reauthenticate", [False, True])
@pytest.mark.parametrize("value", [ACR, None, "0", "urn:aletheia:local-test:MFA", 2, True, [ACR], {"value": ACR}])
def test_acr_request_and_signed_claim_enforcement(tmp_path, key, reauthenticate, value):
    async def scenario():
        policy = AuthPolicy.model_validate({**POLICY.model_dump(), "required_acr": ACR})
        client, provider, attempt, callback = _system(tmp_path, key, policy=policy, reauthenticate=reauthenticate)
        query = parse_qs(urlsplit((await client.authorization_url(attempt)).get_secret_value()).query)
        assert query["acr_values"] == [ACR]
        provider.claims["auth_time"] = int(NOW.timestamp())
        provider.claims["amr"] = ["pwd", "otp"]  # These labels never substitute for the required ACR.
        if value is not None:
            provider.claims["acr"] = value
        if value == ACR:
            assert (await client.exchange(callback, attempt)).email_verified is True
        else:
            with pytest.raises(OidcRejected) as caught:
                await client.exchange(callback, attempt)
            assert caught.value.__suppress_context__ is True
        assert len(provider.requests) == 1
    asyncio.run(scenario())


def test_no_assurance_policy_keeps_existing_password_only_flow_explicit(tmp_path, key):
    async def scenario():
        client, _, attempt, callback = _system(tmp_path, key)
        query = parse_qs(urlsplit((await client.authorization_url(attempt)).get_secret_value()).query)
        assert "acr_values" not in query
        assert (await client.exchange(callback, attempt)).email_verified is True
    asyncio.run(scenario())
