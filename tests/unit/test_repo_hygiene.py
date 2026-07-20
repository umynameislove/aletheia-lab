"""Tests for the public-repository hygiene boundary."""

from pathlib import Path

from scripts.check_repo_hygiene import check


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
    for root_name in ("data", "experiments", "reports"):
        root = tmp_path / root_name
        root.mkdir()
        score_text = "machine result " + "10" + "/" + "10\n"
        (root / "result.md").write_text(score_text, encoding="utf-8")

    errors, warnings, via_git = check(tmp_path, use_git=False)

    assert via_git is False
    assert errors == []
    assert warnings == []
