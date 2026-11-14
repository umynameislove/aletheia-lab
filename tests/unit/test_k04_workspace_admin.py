"""Operator command trust boundaries; database mutations are integration-tested."""
import importlib.util
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "k04_workspace_admin.py"
spec = importlib.util.spec_from_file_location("k04_workspace_admin", SCRIPT)
admin = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = admin
spec.loader.exec_module(admin)


@pytest.mark.parametrize("schema", ["public", "pg_catalog", "information_schema", "a;drop schema x", ""])
def test_operator_refuses_unscoped_or_injected_schema(schema):
    with pytest.raises(SystemExit):
        admin.arguments(["migrate", "--schema", schema])


def test_operator_requires_explicit_issuer_and_identity_file():
    with pytest.raises(SystemExit):
        admin.arguments(["provision", "--schema", "synthetic"])
    value = admin.arguments(["provision", "--schema", "synthetic", "--identity-file", "synthetic.json",
                             "--expected-issuer", "https://identity.example.invalid"])
    assert value.identity_file == Path("synthetic.json")
    assert value.database_url_env == "ALETHEIA_HOSTED_DATABASE_URL"


def test_operator_input_rejects_unknown_grants_and_logs_fixed_failure(monkeypatch, capsys):
    with pytest.raises(ValidationError):
        admin.ProvisioningInput.model_validate({"identity": {}, "display_name": "A", "role": "owner"})
    monkeypatch.delenv("K04_MISSING_TEST_SECRET", raising=False)
    assert admin.main(["migrate", "--schema", "synthetic", "--database-url-env", "K04_MISSING_TEST_SECRET"]) == 1
    assert capsys.readouterr().err == "Workspace administration failed; no credentials or identity details were logged.\n"
