"""Runtime preflight with synthetic values and no running infrastructure."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from aletheia_lab.hosted.runtime import api_server_options, main, preflight
from aletheia_lab.hosted.settings import (
    ConfigurationRejected,
    RuntimeSettings,
    load_runtime_settings,
    resolve_runtime_secrets,
)

ROOT = Path(__file__).resolve().parents[2]
SYNTHETIC_DSN = "postgresql://synthetic:do-not-disclose@db.internal/aletheia"


def _settings(tmp_path, **changes):
    source = {
        "environment": "staging",
        "public_origin": "https://app.internal",
        "identity": {
            "issuer": "https://identity.internal/realms/synthetic",
            "client_id": "synthetic-public-client",
            "redirect_uri": "https://app.internal/v1/auth/callback",
            "client_kind": "public",
        },
        "storage": {
            "database_url_env": "ALETHEIA_HOSTED_DATABASE_URL",
            "object_root": str(tmp_path / "not-created"),
        },
    }
    source.update(changes)
    return RuntimeSettings.model_validate(source)


def _toml(tmp_path):
    target = tmp_path / "runtime.toml"
    target.write_text(
        'environment = "staging"\npublic_origin = "https://app.internal"\n'
        '[identity]\nissuer = "https://identity.internal/realms/synthetic"\n'
        'client_id = "synthetic-public-client"\n'
        'redirect_uri = "https://app.internal/v1/auth/callback"\nclient_kind = "public"\n'
        '[storage]\ndatabase_url_env = "ALETHEIA_HOSTED_DATABASE_URL"\n'
        'expected_schema = "synthetic_app"\nexpected_database_role = "synthetic_api"\n'
        f'object_root = {json.dumps(str(tmp_path / "not-created"))}\n',
        encoding="utf-8",
    )
    return target


def test_preflight_does_not_claim_connectivity_start_workers_or_create_objects(tmp_path):
    report = preflight(_toml(tmp_path), {"ALETHEIA_HOSTED_DATABASE_URL": SYNTHETIC_DSN})
    assert report["configuration_valid"] is True
    assert report["planned_api_processes"] == 1
    assert report["planned_worker_processes"] == 1
    assert report["planned_database_connections"] == 12
    assert report["dependencies_probed"] is False
    assert report["runtime_started"] is False
    assert report["hosted_ready"] is False
    assert not (tmp_path / "not-created").exists()
    assert "do-not-disclose" not in json.dumps(report)
    assert "db.internal" not in json.dumps(report)


def test_api_and_worker_replica_counts_are_both_in_the_db_budget(tmp_path):
    with pytest.raises(ValidationError):
        _settings(tmp_path, api={"processes": 2})
    with pytest.raises(ValidationError):
        _settings(tmp_path, worker={"processes": 4})


@pytest.mark.parametrize("origin", [
    "http://app.internal", "https://app.internal/path", "https://user:password@app.internal",
    "https://app.internal?secret=value", "https://app.internal#fragment", "https://app.internal\\path",
])
def test_rejects_unsafe_public_origins(tmp_path, origin):
    with pytest.raises(ValidationError):
        _settings(tmp_path, public_origin=origin)


def test_callback_must_match_the_registered_public_origin(tmp_path):
    identity = _settings(tmp_path).identity.model_dump()
    identity["redirect_uri"] = "https://other.internal/v1/auth/callback"
    with pytest.raises(ValidationError):
        _settings(tmp_path, identity=identity)


def test_plain_http_requires_explicit_local_mode_and_loopback_urls(tmp_path):
    source = _settings(tmp_path).model_dump()
    source["environment"] = "local"
    source["public_origin"] = "http://127.0.0.1:18766"
    source["identity"]["issuer"] = "http://127.0.0.1:18080/realms/synthetic"
    source["identity"]["redirect_uri"] = "http://127.0.0.1:18766/v1/auth/callback"
    assert RuntimeSettings.model_validate(source).environment == "local"
    source["identity"]["issuer"] = "http://identity.internal"
    with pytest.raises(ValidationError):
        RuntimeSettings.model_validate(source)


def test_unknown_settings_and_invalid_budgets_are_rejected(tmp_path):
    with pytest.raises(ValidationError):
        _settings(tmp_path, api={"proxy_headers": True})
    with pytest.raises(ValidationError):
        _settings(tmp_path, api={"processes": True})
    with pytest.raises(ValidationError):
        _settings(tmp_path, api={"backend_timeout_seconds": float("inf")})
    with pytest.raises(ValidationError):
        _settings(tmp_path, api={"body_idle_timeout_seconds": 20.0, "body_total_timeout_seconds": 10.0})


def test_declared_secrets_are_masked_and_not_taken_from_local_database_url(tmp_path):
    settings = _settings(tmp_path)
    with pytest.raises(ConfigurationRejected):
        resolve_runtime_secrets(settings, {"DATABASE_URL": SYNTHETIC_DSN})
    secrets = resolve_runtime_secrets(settings, {"ALETHEIA_HOSTED_DATABASE_URL": SYNTHETIC_DSN})
    assert secrets.database_url.get_secret_value() == SYNTHETIC_DSN
    assert "do-not-disclose" not in repr(secrets)


@pytest.mark.parametrize("dsn", ["", "sqlite:///local.db", "postgresql://user@host/", "postgresql://host/db"])
def test_invalid_or_wrong_store_dsn_has_a_safe_error(tmp_path, dsn):
    with pytest.raises(ConfigurationRejected) as caught:
        resolve_runtime_secrets(_settings(tmp_path), {"ALETHEIA_HOSTED_DATABASE_URL": dsn})
    assert str(caught.value) == "Invalid hosted configuration: storage.database_url_env"


def test_confidential_client_requires_its_own_declared_secret(tmp_path):
    source = _settings(tmp_path).model_dump()
    source["identity"]["client_kind"] = "confidential"
    with pytest.raises(ValidationError):
        RuntimeSettings.model_validate(source)
    source["identity"]["client_secret_env"] = "ALETHEIA_HOSTED_OIDC_CLIENT_SECRET"
    settings = RuntimeSettings.model_validate(source)
    with pytest.raises(ConfigurationRejected):
        resolve_runtime_secrets(settings, {"ALETHEIA_HOSTED_DATABASE_URL": SYNTHETIC_DSN})
    secrets = resolve_runtime_secrets(settings, {
        "ALETHEIA_HOSTED_DATABASE_URL": SYNTHETIC_DSN,
        "ALETHEIA_HOSTED_OIDC_CLIENT_SECRET": "synthetic-client-secret",
    })
    assert "synthetic-client-secret" not in repr(secrets)


def test_server_plan_has_explicit_proxy_and_logging_policy(tmp_path, monkeypatch):
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "*")
    monkeypatch.setenv("WEB_CONCURRENCY", "999")
    options = api_server_options(_settings(tmp_path))
    assert options["proxy_headers"] is False
    assert options["forwarded_allow_ips"] == ""
    assert options["access_log"] is False
    assert options["workers"] == 1
    assert options["loop"] == "asyncio"
    from uvicorn.protocols.http.h11_impl import H11Protocol

    assert issubclass(options["http"], H11Protocol)
    assert options["http"]._header_timeout_seconds == 5.0
    assert options["reload"] is False


def test_checked_in_template_fails_until_real_values_are_supplied():
    with pytest.raises(ConfigurationRejected):
        load_runtime_settings(ROOT / "configs" / "hosted" / "staging.example.toml")


@pytest.mark.parametrize("content", [b"not valid TOML [", b"\xff", b" " * 65537])
def test_bad_or_oversized_config_is_rejected_without_echoing_input(tmp_path, content):
    config = tmp_path / "bad.toml"
    config.write_bytes(content)
    with pytest.raises(ConfigurationRejected) as caught:
        load_runtime_settings(config)
    assert str(caught.value) == "Invalid hosted configuration: configuration_file"


def test_cli_sanitizes_unknown_fields_and_failure_messages(tmp_path):
    config = _toml(tmp_path)
    with config.open("a", encoding="utf-8") as stream:
        stream.write('\npassword_do_not_disclose = "super-secret-test-value"\n')
    stdout, stderr = io.StringIO(), io.StringIO()
    assert main(["check-config", "--config", str(config)], environment={}, stdout=stdout, stderr=stderr) == 2
    assert stdout.getvalue() == ""
    assert "super-secret-test-value" not in stderr.getvalue()
    assert "password_do_not_disclose" not in stderr.getvalue()


def test_cli_success_is_only_an_offline_configuration_result(tmp_path):
    stdout, stderr = io.StringIO(), io.StringIO()
    result = main(["check-config", "--config", str(_toml(tmp_path))],
                  environment={"ALETHEIA_HOSTED_DATABASE_URL": SYNTHETIC_DSN}, stdout=stdout, stderr=stderr)
    assert result == 0 and stderr.getvalue() == ""
    assert json.loads(stdout.getvalue())["hosted_ready"] is False


@pytest.mark.parametrize("field", ["expected_schema", "expected_database_role"])
@pytest.mark.parametrize("value", ["", "UPPER", "a.b", "a\"b", "has space", "a" * 64, "érole", True])
def test_runtime_binding_rejects_unsafe_identifiers(tmp_path, field, value):
    storage = _settings(tmp_path).storage.model_dump()
    storage.update(expected_schema="synthetic_app", expected_database_role="synthetic_api")
    storage[field] = value
    with pytest.raises(ValidationError):
        _settings(tmp_path, storage=storage)


@pytest.mark.parametrize("schema", ["public", "information_schema", "pg_catalog", "pg_temp_1"])
def test_runtime_binding_requires_an_application_schema(tmp_path, schema):
    storage = _settings(tmp_path).storage.model_dump()
    storage.update(expected_schema=schema, expected_database_role="synthetic_api")
    with pytest.raises(ValidationError):
        _settings(tmp_path, storage=storage)


@pytest.mark.parametrize("field", ["expected_schema", "expected_database_role"])
def test_runtime_binding_is_an_all_or_neither_pair(tmp_path, field):
    storage = _settings(tmp_path).storage.model_dump()
    storage[field] = "synthetic_app"
    with pytest.raises(ValidationError):
        _settings(tmp_path, storage=storage)


def test_staging_loader_requires_binding_but_local_legacy_remains_explicit(tmp_path):
    config = _toml(tmp_path)
    content = config.read_text(encoding="utf-8").replace(
        'expected_schema = "synthetic_app"\nexpected_database_role = "synthetic_api"\n', "")
    config.write_text(content, encoding="utf-8")
    with pytest.raises(ConfigurationRejected) as caught:
        load_runtime_settings(config)
    assert caught.value.fields == ("storage",)
    assert str(caught.value) == "Invalid hosted configuration: storage"
    config.write_text(content.replace('environment = "staging"', 'environment = "local"'), encoding="utf-8")
    assert load_runtime_settings(config).storage.expected_schema is None
    assert _settings(tmp_path).storage.expected_schema is None  # Synthetic direct models are not deployment proof.


def test_binding_identifiers_have_no_postgres_truncation(tmp_path):
    storage = _settings(tmp_path).storage.model_dump()
    storage.update(expected_schema="a" * 63, expected_database_role="b" * 63)
    result = _settings(tmp_path, storage=storage).storage
    assert result.expected_schema == "a" * 63
    assert result.expected_database_role == "b" * 63
