"""Schema checking must not silently omit optional date-time validation."""
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from aletheia_lab.hosted_contract.session_capabilities import Session

ROOT = Path(__file__).resolve().parents[2]


def test_schema_checker_requires_date_time_dependency(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("j01_schema_checker", ROOT / "scripts/check_j01_schema.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "FormatChecker", lambda: SimpleNamespace(checkers={}))
    monkeypatch.setattr(sys, "argv", ["check_j01_schema.py", str(tmp_path)])
    with pytest.raises(RuntimeError, match="date-time checker"):
        module.main()


@pytest.mark.parametrize("value", [None, 0, 1, "true", "false"])
def test_workspace_creation_hint_is_strict_boolean(value):
    import json
    source = json.loads((ROOT / "fixtures/j01/session-proposal.json").read_text(encoding="utf-8"))
    source["can_create_workspace"] = value
    with pytest.raises(ValidationError):
        Session.model_validate_json(json.dumps(source))
