"""Offline policy checks; no IdP, browser, DB or authentication runtime."""

import traceback
from pathlib import Path

import pytest
from pydantic import ValidationError

from aletheia_lab.hosted.settings import RuntimeSettings
from aletheia_lab.hosted_auth.settings import (
    AuthConfigurationRejected,
    AuthPolicy,
    load_auth_policy,
)

ROOT = Path(__file__).resolve().parents[2]


def _policy(**changes):
    return AuthPolicy.model_validate({
        "default_return_to": "/app",
        "return_to_paths": ["/", "/app", "/account"],
        **changes,
    })


def _runtime(tmp_path, **changes):
    return RuntimeSettings.model_validate({
        "environment": "staging",
        "public_origin": "https://app.example.invalid",
        "identity": {
            "issuer": "https://identity.example.invalid/realm",
            "client_id": "synthetic-test-client",
            "redirect_uri": "https://app.example.invalid/auth/callback",
            "client_kind": "public",
        },
        "storage": {
            "database_url_env": "ALETHEIA_HOSTED_DATABASE_URL",
            "object_root": str(tmp_path),
        },
        **changes,
    })


def test_policy_is_internal_immutable_configuration_with_explicit_destinations():
    policy = _policy()
    assert policy.return_to_paths == ("/", "/app", "/account")
    assert policy.rotation_seconds < policy.idle_seconds <= policy.absolute_seconds
    assert policy.require_verified_email is True
    assert policy.require_response_issuer is True
    assert policy.id_token_algorithms == ("RS256",)
    with pytest.raises(ValidationError):
        policy.idle_seconds = 1000


@pytest.mark.parametrize("changes", [
    {"return_to_paths": []},
    {"return_to_paths": ["/app", "/app"]},
    {"default_return_to": "/not-allowed"},
    {"default_return_to": ""},
    {"return_to_paths": ["https://evil.invalid"]},
    {"return_to_paths": ["//evil.invalid"]},
    {"return_to_paths": ["/app/../account"]},
    {"return_to_paths": ["/app?next=https://evil.invalid"]},
    {"return_to_paths": ["/app%2faccount"]},
    {"return_to_paths": [1]},
    {"idle_seconds": True},
    {"rotation_seconds": 900},
    {"absolute_seconds": 600},
    {"login_transaction_seconds": 601},
    {"clock_skew_seconds": 61},
    {"clock_skew_seconds": -1},
    {"idp_timeout_seconds": float("inf")},
    {"idp_timeout_seconds": 0.0},
    {"idp_maximum_inflight": True},
    {"idp_maximum_inflight": 0},
    {"idp_maximum_inflight": 65},
    {"idp_initial_backoff_seconds": 0.0},
    {"idp_maximum_backoff_seconds": float("inf")},
    {"idp_initial_backoff_seconds": 5.0, "idp_maximum_backoff_seconds": 2.0},
    {"admission_namespace": ""},
    {"admission_namespace": "namespace/with/slashes"},
    {"admission_global_attempts": 10001},
    {"admission_subject_attempts": True},
    {"admission_global_attempts": 10, "admission_subject_attempts": 11},
    {"admission_window_seconds": 0},
    {"admission_window_seconds": 3601},
    {"admission_secret_env": "UNRELATED_SECRET"},
    {"id_token_algorithms": []},
    {"id_token_algorithms": ["none"]},
    {"id_token_algorithms": ["HS256"]},
    {"id_token_algorithms": ["RS256", "RS256"]},
    {"role": "owner"},
])
def test_policy_rejects_unsafe_or_inconsistent_configuration(changes):
    with pytest.raises(ValidationError):
        _policy(**changes)


def test_blank_checked_in_template_is_not_a_working_auth_configuration():
    with pytest.raises(AuthConfigurationRejected):
        load_auth_policy(ROOT / "configs" / "hosted" / "k03-auth.example.toml")


def test_explicit_policy_load_normalizes_toml_arrays_without_loading_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("ALETHEIA_HOSTED_OIDC_CLIENT_SECRET", "synthetic-do-not-read")
    policy_file = tmp_path / "policy.toml"
    policy_file.write_text(
        'default_return_to = "/app"\nreturn_to_paths = ["/app"]\n'
        'id_token_algorithms = ["RS256", "ES256"]\n', encoding="utf-8",
    )
    policy = load_auth_policy(policy_file)
    assert policy.return_to_paths == ("/app",)
    assert policy.id_token_algorithms == ("RS256", "ES256")
    assert "synthetic-do-not-read" not in repr(policy)


@pytest.mark.parametrize("payload", [
    b"x" * 16385,
    b"\xff",
    b"[broken",
    b'default_return_to = "synthetic-private-value"',
])
def test_configuration_errors_do_not_echo_input(tmp_path, payload):
    policy_file = tmp_path / "invalid.toml"
    policy_file.write_bytes(payload)
    with pytest.raises(AuthConfigurationRejected) as caught:
        load_auth_policy(policy_file)
    assert str(caught.value) == "Authentication policy is missing or invalid."
    assert "synthetic-private-value" not in repr(caught.value)
    assert caught.value.__suppress_context__ is True


def test_missing_policy_file_has_fixed_safe_error(tmp_path):
    with pytest.raises(AuthConfigurationRejected):
        load_auth_policy(tmp_path / "missing.toml")


def test_oversized_policy_does_not_render_a_sensitive_prior_exception(tmp_path):
    policy_file = tmp_path / "oversized.toml"
    policy_file.write_bytes(b"synthetic-policy-secret" * 1000)
    try:
        raise RuntimeError("synthetic-prior-secret")
    except RuntimeError:
        with pytest.raises(AuthConfigurationRejected) as caught:
            load_auth_policy(policy_file)
    # A real prior exception exists; formatting the safe error must hide it.
    assert caught.value.__context__ is not None
    rendered = "".join(traceback.format_exception(caught.value))
    assert "Authentication policy is missing or invalid." in rendered
    assert "synthetic-prior-secret" not in rendered
    assert "synthetic-policy-secret" not in rendered


def test_runtime_compatibility_uses_existing_k02_settings(tmp_path):
    # Pure validation, not a network probe or a real client registration.
    assert _policy().require_runtime(_runtime(tmp_path)) is None


def test_real_auth_cannot_silently_fall_back_to_insecure_local_cookies(tmp_path):
    runtime = _runtime(
        tmp_path, environment="local", public_origin="http://localhost:18766",
        identity={
            "issuer": "http://localhost:8080/realm",
            "client_id": "synthetic-test-client",
            "redirect_uri": "http://localhost:18766/auth/callback",
            "client_kind": "public",
        },
    )
    with pytest.raises(AuthConfigurationRejected):
        _policy().require_runtime(runtime)


def test_idp_timeout_must_leave_room_in_existing_adapter_deadline(tmp_path):
    with pytest.raises(AuthConfigurationRejected):
        _policy(idp_timeout_seconds=5.0).require_runtime(_runtime(tmp_path))


def test_encoded_callback_path_is_rejected_before_auth_runtime_composition(tmp_path):
    runtime = _runtime(tmp_path)
    identity = runtime.identity.model_copy(update={
        "redirect_uri": "https://app.example.invalid/auth%2fcallback",
    })
    with pytest.raises(AuthConfigurationRejected):
        _policy().require_runtime(runtime.model_copy(update={"identity": identity}))
