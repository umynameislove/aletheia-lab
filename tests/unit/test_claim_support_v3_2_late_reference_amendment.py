from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AMENDMENT = ROOT / "docs/claim-support-v3-2-late-reference-freeze-amendment.md"
DOCUMENTATION = (
    ROOT / "README.md",
    ROOT / "docs/claim-support-validation-v3.md",
)


def test_late_reference_amendment_preserves_the_disclosed_gate() -> None:
    text = AMENDMENT.read_text(encoding="utf-8")
    normalized = " ".join(text.split())

    assert "**PASS WITH GAP**" in text
    assert "support an unqualified `PASS`" in normalized
    assert "`main_packet_release_authorized=false`" in text
    assert "does not authorize external delivery" in normalized
    assert "does not rewrite them to simulate the intended chronology" in normalized
    assert "five graders are correlated runs from one model family" in normalized
    assert "cannot be pooled with human votes" in normalized
    assert "minimum_macro_f1" not in text
    assert "macro-F1 at least `0.80`" in text
    assert "zero cases where a reference-contradicted claim" in text


def test_late_reference_amendment_discloses_only_safe_rater_slots() -> None:
    text = AMENDMENT.read_text(encoding="utf-8")
    personal_root = "/" + "Users" + "/"

    forbidden = (
        personal_root,
        "/Downloads/",
        "completed-submission",
        "completed-reference-submission",
        "blind-claim-",
    )
    assert all(value not in text for value in forbidden)
    assert "Rater-slot 1" in text
    assert "Rater-slot 2" in text


def test_public_docs_link_to_the_late_reference_amendment() -> None:
    target = "claim-support-v3-2-late-reference-freeze-amendment.md"

    assert AMENDMENT.is_file()
    assert all(target in path.read_text(encoding="utf-8") for path in DOCUMENTATION)
