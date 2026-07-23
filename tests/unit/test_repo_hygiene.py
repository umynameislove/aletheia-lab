"""Tests for the public-repository hygiene boundary."""

import importlib.util
from pathlib import Path
from types import ModuleType


def _load_hygiene_module() -> ModuleType:
    script = Path(__file__).resolve().parents[2] / "scripts" / "check_repo_hygiene.py"
    spec = importlib.util.spec_from_file_location("aletheia_repo_hygiene", script)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load repository-hygiene script: {script}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check = _load_hygiene_module().check


def test_filesystem_scan_rejects_tracking_and_reports_cache_once(tmp_path: Path) -> None:
    tracking = tmp_path / "tracking"
    tracking.mkdir()
    (tracking / "notes.md").write_text("private plan\n", encoding="utf-8")
    cache = tmp_path / ".pytest_cache"
    cache.mkdir()
    (cache / "nodeids").write_text("generated\n", encoding="utf-8")

    errors, warnings, via_git = check(tmp_path, use_git=False)

    assert via_git is False
    assert errors == [
        "[TRACKING DIR]  tracking/  -> internal tracking must remain outside the repository"
    ]
    assert warnings == ["[JUNK DIR]      .pytest_cache/"]


def test_filesystem_scan_excludes_private_generated_artifact_roots(tmp_path: Path) -> None:
    for root_name in ("data", "experiments"):
        root = tmp_path / root_name
        root.mkdir()
        score_text = "machine result " + "10" + "/" + "10\n"
        (root / "result.md").write_text(score_text, encoding="utf-8")

    errors, warnings, via_git = check(tmp_path, use_git=False)

    assert via_git is False
    assert errors == []
    assert warnings == []


def test_filesystem_scan_includes_public_reports(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "summary.md").write_text(
        "## Authorized novelty statement\n", encoding="utf-8"
    )

    errors, warnings, via_git = check(tmp_path, use_git=False)

    assert via_git is False
    assert errors == [
        "[SELF-GRADE/AI] reports/summary.md  -> "
        "matched '\\bauthorized\\s+novelty\\s+statement\\b'"
    ]
    assert warnings == []


def test_filesystem_scan_rejects_internal_workflow_artifacts(tmp_path: Path) -> None:
    (tmp_path / "job.md").write_text("implementation assignment\n", encoding="utf-8")
    (tmp_path / "review_to_fix.md").write_text("findings\n", encoding="utf-8")
    (tmp_path / "public.md").write_text("Auditor đúng; sửa ngay.\n", encoding="utf-8")

    errors, warnings, via_git = check(tmp_path, use_git=False)

    assert via_git is False
    assert errors == [
        "[SELF-GRADE/AI] public.md  -> matched '\\bauditor\\s+đ[uú]ng\\b'",
        "[TRACKING FILE] job.md  -> move internal tracking outside the repository",
        "[TRACKING FILE] review_to_fix.md  -> move internal tracking outside the repository",
    ]
    assert warnings == []


def test_filesystem_scan_distinguishes_results_from_self_grading(tmp_path: Path) -> None:
    (tmp_path / "result.md").write_text(
        "Missing-key sensitivity: 10/10.\n", encoding="utf-8"
    )

    errors, warnings, _ = check(tmp_path, use_git=False)
    assert errors == []
    assert warnings == []

    (tmp_path / "self_grade.md").write_text(
        "Project readiness score: 10/10.\n", encoding="utf-8"
    )
    errors, _, _ = check(tmp_path, use_git=False)
    assert errors == [
        "[SELF-GRADE/AI] self_grade.md  -> matched "
        "'\\b(score|rating|readiness|đánh\\s*giá)\\b[^\\n]{0,40}"
        "\\b\\d+(?:\\.\\d+)?(?:\\s*-\\s*\\d+(?:\\.\\d+)?)?\\s*/\\s*10\\b'"
    ]
