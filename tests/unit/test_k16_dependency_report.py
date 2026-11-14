"""Dependency graph markers/extras and secret-free failed provenance."""

from __future__ import annotations

import importlib.util
import json
from email.message import Message
from pathlib import Path, PurePosixPath, PureWindowsPath
from types import SimpleNamespace

import pytest


def _module():
    path = Path(__file__).resolve().parents[2] / "scripts/k16_dependency_report.py"
    spec = importlib.util.spec_from_file_location("resolved_test_inventory", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _project(root, dependencies):
    (root / "pyproject.toml").write_text(
        '[project]\nname="aletheia-lab"\nversion="0.1.0"\ndependencies='
        + json.dumps(dependencies)
        + "\n[project.optional-dependencies]\nhosted=[]\n"
    )


def test_graph_honors_markers_expands_extras_once_and_keeps_cycles_bounded(tmp_path, monkeypatch):
    module = _module()
    _project(tmp_path, ["base[fast]>=1", 'absent==1; sys_platform=="impossible_os"'])
    calls = []

    def distribution(name):
        calls.append(name)
        rows = {
            "base": ("1.2", ['child==2; extra=="fast"', "cycle==1"]),
            "child": ("2", []),
            "cycle": ("1", ["base[fast]>=1"]),
        }
        version, requires = rows[name]
        metadata = Message()
        metadata["License-Expression"] = "MIT"
        return SimpleNamespace(
            version=version,
            requires=requires,
            metadata=metadata,
            files=[],
            locate_file=lambda x: tmp_path,
            read_text=lambda x: "Synthetic metadata",
        )

    monkeypatch.setattr(module, "distribution", distribution)
    inventory, spdx = module.build(tmp_path)
    assert {p["name"] for p in inventory["packages"]} == {"base", "child", "cycle"}
    assert "absent" not in calls and len(calls) < 10
    assert ["base", "child"] in inventory["dependency_edges"] and ["cycle", "base"] in inventory[
        "dependency_edges"
    ]
    assert all(p["license_declared"] == "MIT" for p in inventory["packages"])
    assert len(spdx["packages"]) == 4 and spdx["spdxVersion"] == "SPDX-2.3"
    assert str(tmp_path) not in json.dumps(inventory)


def test_installed_version_mismatch_and_direct_url_do_not_become_frozen_success(
    tmp_path, monkeypatch
):
    module = _module()
    _project(tmp_path, ["base==2"])
    monkeypatch.setattr(module, "distribution", lambda name: SimpleNamespace(version="1"))
    with pytest.raises(ValueError, match="version constraints"):
        module.build(tmp_path)
    _project(tmp_path, ["base @ https://user:PRIVATE_SECRET@example.invalid/private.whl"])
    with pytest.raises(ValueError, match="Direct URL"):
        module.build(tmp_path)


def test_dependency_report_cli_failure_does_not_echo_credential_url(tmp_path, monkeypatch, capsys):
    module = _module()
    _project(tmp_path, ["base @ https://user:PRIVATE_SECRET@example.invalid/private.whl"])
    monkeypatch.setattr(
        module.sys, "argv", ["report", "--root", str(tmp_path), "--output", str(tmp_path / "out")]
    )
    assert module.main() == 1 and not (tmp_path / "out").exists()
    output = capsys.readouterr()
    assert output.out == "" and json.loads(output.err) == {"error": "dependency_report_unavailable"}
    assert "PRIVATE" not in output.err


@pytest.mark.parametrize("record_path", [PurePosixPath, PureWindowsPath])
def test_exact_reviewed_notice_set_and_changed_extra_notice_fail_closed(tmp_path, monkeypatch, record_path):
    from importlib.metadata import distribution as installed

    module = _module()
    original = installed("itsdangerous")
    notice_name = "itsdangerous-2.2.0.dist-info/LICENSE.txt"
    data = Path(original.locate_file(notice_name)).read_bytes()
    notice = tmp_path / notice_name
    notice.parent.mkdir()
    notice.write_bytes(data)
    _project(tmp_path, ["itsdangerous==2.2.0"])
    dist = SimpleNamespace(
        version="2.2.0",
        requires=[],
        metadata=original.metadata,
        files=[record_path(notice_name)],
        locate_file=lambda path: tmp_path / path,
        read_text=lambda name: "Synthetic copied metadata",
    )
    monkeypatch.setattr(module, "distribution", lambda name: dist)
    result, _ = module.build(tmp_path)
    assert result["packages"][0]["license_declared"] == "BSD-3-Clause"
    notice.write_bytes(data + b"\nADDITIONAL_TERMS\n")
    changed, _ = module.build(tmp_path)
    assert changed["packages"][0]["license_declared"] == "NOASSERTION"
    assert (
        changed["packages"][0]["license_identification"]["status"] == "reviewed_notice_set_changed"
    )
    notice.write_bytes(data)
    extra = tmp_path / "itsdangerous/vendor/LICENSE"
    extra.parent.mkdir(parents=True)
    extra.write_text("Unreviewed bundled notice")
    dist.files.append(record_path("itsdangerous/vendor/LICENSE"))
    changed, _ = module.build(tmp_path)
    assert changed["packages"][0]["license_declared"] == "NOASSERTION"


def test_custom_notice_references_preserve_full_multinotice_text_and_are_not_bsd_only(monkeypatch):
    module = _module()
    ledger = json.loads(Path(module.__file__).with_name("k16_license_reviews.json").read_text())
    for row in ledger["reviews"]:
        if row["license_ref"] is None:
            continue
        documents = [
            {"file": name, "sha256": digest} for name, digest in row["license_files_sha256"].items()
        ]
        texts = {name: "Complete synthetic notice " + name for name in row["license_files_sha256"]}
        if row["platform"]:
            monkeypatch.setattr(module.sys, "platform", row["platform"])
            monkeypatch.setattr(module.platform, "machine", lambda machine=row["machine"]: machine)
        identified, extracted = module._review(row["name"], row["version"], documents, texts)
        assert (
            row["license_ref"] in identified["expression"]
            and identified["expression"] != "BSD-3-Clause"
        )
        assert extracted["licenseId"] == row["license_ref"]
        assert all(text in extracted["extractedText"] for text in texts.values())
        unknown, no_reference = module._review(
            row["name"], row["version"], documents, texts, complete=False
        )
        assert unknown["expression"] == "NOASSERTION" and no_reference is None


def test_notice_collection_includes_actual_credits_and_notice_filename_conventions():
    module = _module()
    for name in (
        "NOTICE.txt",
        "NOTICE",
        "AUTHORS",
        "AUTHORS.md",
        "COPYRIGHT",
        "COPYING_QHULL.txt",
        "bitstream-vera-license.txt",
    ):
        assert module._notice_file(name)
    assert not module._notice_file("ordinary_module.py")
