"""Leakage checks for evidence bundles."""

from __future__ import annotations

from collections.abc import Iterable

from aletheia_lab.evidence.schema import EvidenceBundle


def normalize_text(value: str) -> str:
    """Normalize text for simple leakage scanning."""

    return " ".join(value.casefold().split())


def find_forbidden_terms(text: str, forbidden_terms: Iterable[str]) -> list[str]:
    """Return forbidden terms that appear in text."""

    normalized_text = normalize_text(text)
    matches: list[str] = []
    for term in forbidden_terms:
        normalized_term = normalize_text(term)
        if normalized_term and normalized_term in normalized_text:
            matches.append(term)
    return matches


def bundle_text(bundle: EvidenceBundle) -> str:
    """Flatten visible evidence into text."""

    items = [
        *bundle.allowed_evidence,
        *bundle.counterfactual_evidence,
    ]
    return "\n".join(f"{item.title}\n{item.content}" for item in items if item.visible_to_diagnoser)


def bundle_has_leakage(bundle: EvidenceBundle, forbidden_terms: Iterable[str]) -> bool:
    """Return true when visible evidence leaks forbidden answer-key terms."""

    return bool(find_forbidden_terms(bundle_text(bundle), forbidden_terms))
