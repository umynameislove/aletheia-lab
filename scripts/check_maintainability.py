"""Enforce frozen complexity, module-size, hashing, and publication ownership budgets."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Final

_ROOT: Final = Path(__file__).resolve().parents[1]
_DEFAULT_CONFIG: Final = _ROOT / "configs" / "maintainability_budget.json"
_COMPLEXITY_SCORE = re.compile(r"\((\d+) > \d+\)$")


def _load_config(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("maintainability configuration must be a JSON object")
    if payload.get("schema_version") != "maintainability-budget/v1":
        raise ValueError("unsupported maintainability configuration schema")
    return payload


def _source_files(root: Path) -> tuple[Path, ...]:
    return tuple(sorted((root / "src" / "aletheia_lab").rglob("*.py")))


def _package_for(root: Path, path: Path) -> str:
    relative = path.resolve().relative_to((root / "src" / "aletheia_lab").resolve())
    return relative.parts[0] if len(relative.parts) > 1 else "__root__"


def _ruff_complexity_diagnostics(root: Path) -> list[dict[str, object]]:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--select",
            "C901",
            "--output-format",
            "json",
            "src/aletheia_lab",
        ],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode not in {0, 1}:
        raise RuntimeError(f"ruff complexity scan failed: {completed.stderr.strip()}")
    parsed = json.loads(completed.stdout or "[]")
    if not isinstance(parsed, list) or not all(isinstance(item, dict) for item in parsed):
        raise RuntimeError("ruff complexity output has an unexpected shape")
    return parsed


def _complexity_findings(
    root: Path,
    diagnostics: list[dict[str, object]],
    policy: dict[str, object],
) -> tuple[list[str], dict[str, object]]:
    package_counts: Counter[str] = Counter()
    scores: list[int] = []
    for diagnostic in diagnostics:
        filename = diagnostic.get("filename")
        message = diagnostic.get("message")
        if not isinstance(filename, str) or not isinstance(message, str):
            raise RuntimeError("ruff diagnostic omits filename or message")
        package_counts[_package_for(root, Path(filename))] += 1
        match = _COMPLEXITY_SCORE.search(message)
        if match is None:
            raise RuntimeError(f"cannot parse C901 score: {message}")
        scores.append(int(match.group(1)))

    maximum_total = policy.get("maximum_total_violations")
    maximum_score = policy.get("maximum_single_score")
    budgets = policy.get("package_violation_budgets")
    if (
        not isinstance(maximum_total, int)
        or not isinstance(maximum_score, int)
        or not isinstance(budgets, dict)
        or not all(
            isinstance(key, str) and isinstance(value, int) for key, value in budgets.items()
        )
    ):
        raise ValueError("complexity policy is malformed")

    findings: list[str] = []
    if len(diagnostics) > maximum_total:
        findings.append(f"C901 total {len(diagnostics)} exceeds frozen budget {maximum_total}")
    observed_maximum = max(scores, default=0)
    if observed_maximum > maximum_score:
        findings.append(f"C901 score {observed_maximum} exceeds frozen maximum {maximum_score}")
    for package, count in sorted(package_counts.items()):
        budget = budgets.get(package)
        if not isinstance(budget, int):
            findings.append(f"C901 violations appeared in unbudgeted package {package}: {count}")
        elif count > budget:
            findings.append(f"C901 package {package} has {count} violations; budget is {budget}")
    return findings, {
        "total_violations": len(diagnostics),
        "maximum_score": observed_maximum,
        "package_counts": dict(sorted(package_counts.items())),
    }


def _module_size_findings(
    root: Path,
    files: tuple[Path, ...],
    policy: dict[str, object],
) -> tuple[list[str], dict[str, int]]:
    maximum = policy.get("maximum_lines_without_exemption")
    exemptions = policy.get("exemptions")
    if not isinstance(maximum, int) or not isinstance(exemptions, dict):
        raise ValueError("module-size policy is malformed")

    observed: dict[str, int] = {}
    findings: list[str] = []
    for path in files:
        relative = path.relative_to(root).as_posix()
        line_count = len(path.read_text(encoding="utf-8").splitlines())
        if line_count <= maximum:
            continue
        observed[relative] = line_count
        exemption = exemptions.get(relative)
        if not isinstance(exemption, dict):
            findings.append(
                f"module {relative} has {line_count} lines without a reviewed exemption"
            )
            continue
        budget = exemption.get("maximum_lines")
        rationale = exemption.get("rationale")
        if not isinstance(budget, int) or not isinstance(rationale, str) or not rationale.strip():
            findings.append(f"module exemption is incomplete: {relative}")
        elif line_count > budget:
            findings.append(f"module {relative} has {line_count} lines; frozen budget is {budget}")

    stale = sorted(set(exemptions) - set(observed))
    if stale:
        findings.append(f"module exemptions are stale or unnecessary: {', '.join(stale)}")
    return findings, dict(sorted(observed.items()))


def _direct_sha256_calls(path: Path) -> int:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return sum(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "hashlib"
        and node.func.attr == "sha256"
        for node in ast.walk(tree)
    )


def _hash_findings(
    root: Path,
    files: tuple[Path, ...],
    policy: dict[str, object],
) -> tuple[list[str], dict[str, int]]:
    budgets = policy.get("package_direct_sha256_budgets")
    if not isinstance(budgets, dict) or not all(
        isinstance(key, str) and isinstance(value, int) for key, value in budgets.items()
    ):
        raise ValueError("hash-ownership policy is malformed")
    counts: Counter[str] = Counter()
    for path in files:
        counts[_package_for(root, path)] += _direct_sha256_calls(path)
    counts = Counter({key: value for key, value in counts.items() if value})
    findings: list[str] = []
    for package, count in sorted(counts.items()):
        budget = budgets.get(package)
        if not isinstance(budget, int):
            findings.append(
                f"direct SHA-256 logic appeared in unbudgeted package {package}: {count}"
            )
        elif count > budget:
            findings.append(
                f"direct SHA-256 calls in {package} increased to {count}; budget is {budget}"
            )
    return findings, dict(sorted(counts.items()))


def _os_link_calls(path: Path) -> int:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return sum(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "os"
        and node.func.attr == "link"
        for node in ast.walk(tree)
    )


def _publication_findings(
    root: Path,
    files: tuple[Path, ...],
    policy: dict[str, object],
) -> tuple[list[str], dict[str, object]]:
    owner = policy.get("immutable_file_primitive_owner")
    consumers = policy.get("required_immutable_file_consumers")
    delegates = policy.get("allowed_atomic_create_delegates")
    if (
        not isinstance(owner, str)
        or not isinstance(consumers, list)
        or not all(isinstance(item, str) for item in consumers)
        or not isinstance(delegates, list)
        or not all(isinstance(item, str) for item in delegates)
    ):
        raise ValueError("publication-ownership policy is malformed")

    link_calls = {
        path.relative_to(root).as_posix(): count
        for path in files
        if (count := _os_link_calls(path))
    }
    findings: list[str] = []
    if link_calls != {owner: 1}:
        findings.append(f"os.link ownership differs from the frozen core: {link_calls}")
    for consumer in consumers:
        path = root / consumer
        if not path.is_file() or "publish_immutable_file" not in path.read_text(encoding="utf-8"):
            findings.append(f"immutable publication consumer bypassed the shared core: {consumer}")

    observed_delegates: list[str] = []
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if any(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "_atomic_create"
            for node in ast.walk(tree)
        ):
            observed_delegates.append(path.relative_to(root).as_posix())
    if sorted(observed_delegates) != sorted(delegates):
        findings.append(
            "atomic-create delegate inventory changed: " + ", ".join(sorted(observed_delegates))
        )
    return findings, {
        "os_link_calls": link_calls,
        "atomic_create_delegates": sorted(observed_delegates),
    }


def evaluate(root: Path, config: dict[str, object]) -> dict[str, object]:
    files = _source_files(root)
    complexity_policy = config.get("complexity")
    module_policy = config.get("module_size")
    hash_policy = config.get("hash_ownership")
    publication_policy = config.get("publication_ownership")
    if not all(
        isinstance(item, dict)
        for item in (complexity_policy, module_policy, hash_policy, publication_policy)
    ):
        raise ValueError("maintainability configuration sections are malformed")

    complexity_findings, complexity = _complexity_findings(
        root,
        _ruff_complexity_diagnostics(root),
        complexity_policy,  # type: ignore[arg-type]
    )
    size_findings, oversized = _module_size_findings(
        root,
        files,
        module_policy,  # type: ignore[arg-type]
    )
    hash_findings, hashes = _hash_findings(root, files, hash_policy)  # type: ignore[arg-type]
    publication_findings, publication = _publication_findings(
        root,
        files,
        publication_policy,  # type: ignore[arg-type]
    )
    findings = [
        *complexity_findings,
        *size_findings,
        *hash_findings,
        *publication_findings,
    ]
    receipt: dict[str, object] = {
        "schema_version": "maintainability-audit/v1",
        "status": "pass" if not findings else "blocked",
        "findings": findings,
        "complexity": complexity,
        "oversized_modules": oversized,
        "direct_sha256_calls": hashes,
        "publication": publication,
    }
    canonical = json.dumps(receipt, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    receipt["audit_sha256"] = hashlib.sha256(canonical.encode("ascii")).hexdigest()
    return receipt


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=_DEFAULT_CONFIG)
    return parser


def main() -> int:
    args = _parser().parse_args()
    receipt = evaluate(_ROOT, _load_config(args.config))
    print(json.dumps(receipt, ensure_ascii=True, separators=(",", ":"), sort_keys=True))
    return 0 if receipt["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
