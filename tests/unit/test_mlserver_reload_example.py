"""Dependency-light checks for the public standalone configuration and source."""

from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "examples" / "mlserver-reload-boundary"


def test_native_example_has_file_based_serial_no_cache_settings() -> None:
    model = json.loads((ROOT / "model-settings.json").read_bytes())
    settings = json.loads((ROOT / "settings.json").read_bytes())
    assert model == {
        "name": "probe",
        "implementation": "runtime.Probe",
        "cache_enabled": False,
        "max_batch_size": 0,
    }
    assert settings["host"] == "127.0.0.1"
    assert settings["parallel_workers"] == 0
    assert settings["cache_enabled"] is False
    assert settings["kafka_enabled"] is False
    assert settings["metrics_endpoint"] is None


def test_example_uses_ordinary_import_and_retains_component_markers() -> None:
    runtime = ast.parse((ROOT / "runtime.py").read_text(encoding="utf-8"))
    imports = {
        name.name
        for node in ast.walk(runtime)
        if isinstance(node, ast.Import)
        for name in node.names
    }
    assert imports == {"os", "uuid", "helper"}
    assigned = {
        node.targets[0].id: ast.literal_eval(node.value)
        for node in runtime.body
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
    }
    assert assigned == {"RUNTIME_OFFSET": 0, "RUNTIME_REVISION": "runtime-r1"}
    helper = ast.parse((ROOT / "helper.py").read_text(encoding="utf-8"))
    initial = {
        node.targets[0].id: ast.literal_eval(node.value)
        for node in helper.body
        if isinstance(node, ast.Assign)
    }
    assert initial == {"HELPER_VALUE": 1, "HELPER_REVISION": "helper-r1"}
    calls = {ast.unparse(node.func) for node in ast.walk(runtime) if isinstance(node, ast.Call)}
    assert "os.getpid" in calls and "uuid.uuid4" in calls
    assert not any("reload" in call or "aletheia" in call for call in calls)
