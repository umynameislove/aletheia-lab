"""Property-based security invariants for local project file ingestion boundaries.

These properties verify that security oracle functions maintain their contracts
across a wide range of generated inputs. All oracles are self-contained and
independent of any production ingestion code.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import PurePosixPath, PureWindowsPath
from typing import Final

from hypothesis import assume, given
from hypothesis import strategies as st

# ---------------------------------------------------------------------------
# Oracle functions (redefined locally — no cross-test-module imports per convention)
# ---------------------------------------------------------------------------

_CONTROL_RE: Final[re.Pattern[str]] = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_NUL_RE: Final[re.Pattern[str]] = re.compile(r"\x00")

_SYNTHETIC_TOKEN: Final = "synth-test-token-aGVsbG8gd29ybGQ"
_TOKEN_PATTERN: Final[re.Pattern[str]] = re.compile(r"synth-test-token-[A-Za-z0-9+/=]+")


def _is_candidate_string_safe(candidate: str) -> bool:
    """Independent oracle: pure-path safety check for path strings."""
    if not candidate or candidate != candidate.strip():
        return False
    if _NUL_RE.search(candidate) or _CONTROL_RE.search(candidate):
        return False
    if "\\" in candidate:
        return False
    if PureWindowsPath(candidate).drive:
        return False
    posix = PurePosixPath(candidate)
    if posix.is_absolute():
        return False
    # "." has empty .parts in PurePosixPath; check explicitly
    if candidate in {".", ".."}:
        return False
    if any(part in {".", ".."} for part in posix.parts):
        return False
    return candidate == posix.as_posix()


def _redact(text: str) -> str:
    return _TOKEN_PATTERN.sub("[REDACTED]", text)


# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------

# Safe path segment: alphanumeric plus hyphens and underscores, no leading hyphen
_safe_segment: st.SearchStrategy[str] = st.text(
    alphabet=st.characters(
        whitelist_categories=("Lu", "Ll", "Nd"),
        whitelist_characters="-_",
    ),
    min_size=1,
    max_size=16,
).filter(lambda s: s[0] not in "-_")

# Relative paths: 1–4 safe segments joined by forward slash
_relative_path: st.SearchStrategy[str] = st.lists(
    _safe_segment, min_size=1, max_size=4
).map("/".join)

# Traversal path variants
_traversal_path: st.SearchStrategy[str] = st.one_of(
    st.just(".."),
    st.just("../secret"),
    st.just("subdir/../.."),
    _relative_path.map(lambda p: f"../{p}"),
    _relative_path.map(lambda p: f"{p}/../../outside"),
)

# Absolute path variants (POSIX and Windows)
_absolute_path: st.SearchStrategy[str] = st.one_of(
    st.just("/etc/passwd"),
    st.just("/root/.ssh/id_rsa"),
    st.just("C:\\Windows\\System32"),
    st.just("C:/Windows/System32"),
    st.just("\\\\server\\share\\file.txt"),
    st.just("//server/share/file"),
    st.just("C:relative.txt"),
)

# Text with embedded synthetic token
_text_with_token: st.SearchStrategy[str] = st.builds(
    lambda pre, suf: f"{pre}{_SYNTHETIC_TOKEN}{suf}",
    pre=st.text(max_size=40, alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd"), whitelist_characters=" _=")),
    suf=st.text(max_size=40, alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd"), whitelist_characters=" _=")),
)


# ---------------------------------------------------------------------------
# Path containment properties
# ---------------------------------------------------------------------------


@given(path=_traversal_path)
def test_traversal_path_always_rejected_by_oracle(path: str) -> None:
    """No traversal path variant must ever pass the pure-path containment oracle."""
    assert not _is_candidate_string_safe(path), (
        f"traversal path must be rejected: {path!r}"
    )


@given(path=_absolute_path)
def test_absolute_path_always_rejected_by_oracle(path: str) -> None:
    """No absolute path variant (POSIX or Windows) must ever pass the oracle."""
    assert not _is_candidate_string_safe(path), (
        f"absolute path must be rejected: {path!r}"
    )


@given(path=_relative_path)
def test_safe_relative_path_satisfies_structural_invariants(path: str) -> None:
    """Any path accepted by the oracle must satisfy all structural safety invariants."""
    assume(_is_candidate_string_safe(path))
    posix = PurePosixPath(path)
    # Must be relative
    assert not posix.is_absolute()
    # Must contain no traversal or current-dir segments
    assert all(part not in {".", ".."} for part in posix.parts)
    # Must have at least one component
    assert len(posix.parts) >= 1
    # Must contain no backslash
    assert "\\" not in path
    # Must have no Windows drive
    assert not PureWindowsPath(path).drive


@given(path=_relative_path)
def test_path_oracle_is_deterministic(path: str) -> None:
    """The oracle must return the same result for the same input on repeated calls."""
    result_a = _is_candidate_string_safe(path)
    result_b = _is_candidate_string_safe(path)
    assert result_a == result_b


@given(
    base=_relative_path,
    segment=st.just(".."),
)
def test_appending_traversal_to_safe_path_always_rejected(base: str, segment: str) -> None:
    """Adding a traversal component to any safe path must cause rejection."""
    candidate = f"{base}/{segment}/outside"
    assert not _is_candidate_string_safe(candidate)


# ---------------------------------------------------------------------------
# Secret redaction properties
# ---------------------------------------------------------------------------


@given(text=st.text(max_size=200))
def test_redaction_is_idempotent_on_arbitrary_text(text: str) -> None:
    """Applying redaction twice must produce the same result as applying it once."""
    once = _redact(text)
    twice = _redact(once)
    assert once == twice


@given(text=_text_with_token)
def test_synthetic_token_absent_from_redacted_output(text: str) -> None:
    """The raw synthetic token must not appear in any redacted output."""
    redacted = _redact(text)
    assert _SYNTHETIC_TOKEN not in redacted


@given(text=_text_with_token)
def test_redacted_output_checksum_differs_from_raw(text: str) -> None:
    """The SHA-256 of redacted content must differ from the SHA-256 of raw content."""
    assume(_SYNTHETIC_TOKEN in text)
    redacted = _redact(text)
    raw_hash = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()
    redacted_hash = hashlib.sha256(redacted.encode("utf-8")).hexdigest()
    assert raw_hash != redacted_hash


# ---------------------------------------------------------------------------
# Canonical identity properties
# ---------------------------------------------------------------------------


@given(path=_relative_path)
def test_safe_path_oracle_result_independent_of_call_order(path: str) -> None:
    """The oracle's decision must not depend on any mutable state or call order."""
    results = [_is_candidate_string_safe(path) for _ in range(3)]
    assert len(set(results)) == 1, "oracle must be stateless and consistent"
