"""Run controlled temporary mutations against critical evaluation invariants."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Final

_ROOT: Final = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class _Mutation:
    name: str
    source: str
    replacements: tuple[tuple[str, str], ...]
    target: str


_MUTATIONS: Final = (
    _Mutation(
        name="authorization_guard_removed",
        source="aletheia_lab/evaluation/structural_closeout.py",
        replacements=(("    _check_authorization(checked_plan, findings)\n", ""),),
        target=(
            "tests/unit/test_evaluation_structural_closeout.py::"
            "test_current_authorization_is_explicit_and_can_block_closeout"
        ),
    ),
    _Mutation(
        name="nested_visibility_scan_removed",
        source="aletheia_lab/context/evaluation_context.py",
        replacements=(
            (
                "                nested_violation = inspect(value[key])\n",
                "                nested_violation = None\n",
            ),
        ),
        target=(
            "tests/unit/test_context_evaluation_boundary.py::"
            "test_threat_corpus_is_rejected_at_any_nested_depth"
        ),
    ),
    _Mutation(
        name="retry_context_binding_removed",
        source="aletheia_lab/model_gateway/runtime.py",
        replacements=(
            (
                "        context_sha256=initial.context_sha256,\n",
                '        context_sha256="0" * 64,\n',
            ),
            (
                "    if attempt.request_identity_sha256 != initial.request_identity_sha256:\n"
                '        raise GatewayContractError("retry changed immutable request identity")\n',
                "",
            ),
        ),
        target=(
            "tests/unit/test_model_gateway_runtime.py::"
            "test_transient_error_retries_with_only_ordinal_changed"
        ),
    ),
    _Mutation(
        name="adapter_hard_deadline_removed",
        source="aletheia_lab/model_gateway/runtime.py",
        replacements=(
            (
                "        if remaining_ns <= 0:\n"
                '            return _SupervisedInvocation("timed_out")\n',
                "        if remaining_ns <= 0:\n            remaining_ns = 1_000_000_000\n",
            ),
        ),
        target=(
            "tests/unit/test_model_gateway_runtime.py::"
            "test_blocking_adapter_returns_by_hard_deadline_and_discards_late_response"
        ),
    ),
    _Mutation(
        name="inflight_cancellation_removed",
        source="aletheia_lab/model_gateway/runtime.py",
        replacements=(
            (
                "            if cancellation.is_cancelled():\n"
                '                return _SupervisedInvocation("cancelled")\n',
                "            if False and cancellation.is_cancelled():\n"
                '                return _SupervisedInvocation("cancelled")\n',
            ),
        ),
        target=(
            "tests/unit/test_model_gateway_runtime.py::"
            "test_cancellation_interrupts_gateway_wait_for_blocking_adapter"
        ),
    ),
    _Mutation(
        name="immutable_overwrite_allowed",
        source="aletheia_lab/filesystem.py",
        replacements=(
            (
                "    raise ImmutablePublicationConflictError(\n"
                '        f"refusing to overwrite non-identical immutable bytes: {destination}"\n'
                "    )\n",
                '    destination.write_bytes(payload)\n    return "created"\n',
            ),
        ),
        target=(
            "tests/unit/test_evaluation_attempt_store.py::"
            "test_overwrite_with_different_immutable_bytes_is_blocked"
        ),
    ),
    _Mutation(
        name="context_hash_reconciliation_removed",
        source="aletheia_lab/evaluation/structural_closeout.py",
        replacements=(
            (
                "        (inventory.context_sha256 == expectation.context_sha256, "
                '"context_mismatch"),\n',
                '        (True, "context_mismatch"),\n',
            ),
        ),
        target=(
            "tests/unit/test_evaluation_structural_closeout.py::"
            "test_manifest_supplied_context_equality_rule_is_enforced"
        ),
    ),
    _Mutation(
        name="partial_store_reported_terminal",
        source="aletheia_lab/evaluation/_attempt_store/store.py",
        replacements=(
            (
                "        return terminal is not None\n",
                "        return terminal is not None or "
                "bool(self._load_chain(request_identity_sha256)[0])\n",
            ),
        ),
        target=(
            "tests/unit/test_evaluation_attempt_store.py::"
            "test_crash_after_raw_response_does_not_mint_terminal_state"
        ),
    ),
    _Mutation(
        name="parse_failure_excluded",
        source="aletheia_lab/evaluation/structural_closeout.py",
        replacements=(
            (
                "    observed_by_request = {\n"
                "        item.request_identity_sha256: item for item in inventories\n"
                "    }\n",
                "    observed_by_request = {\n"
                "        item.request_identity_sha256: item\n"
                "        for item in inventories\n"
                '        if item.gateway_status != "parse_failed"\n'
                "    }\n",
            ),
        ),
        target=(
            "tests/unit/test_evaluation_structural_closeout.py::"
            "test_parse_failure_remains_reconciled_and_uninterpreted"
        ),
    ),
    _Mutation(
        name="scientific_default_added",
        source="aletheia_lab/evaluation/structural_closeout.py",
        replacements=(
            (
                "    assert_no_scientific_closeout_fields(fields)\n",
                '    fields["threshold"] = 0.5\n    assert_no_scientific_closeout_fields(fields)\n',
            ),
        ),
        target=(
            "tests/unit/test_evaluation_structural_closeout.py::"
            "test_complete_store_has_deterministic_permutation_invariant_read_only_receipt"
        ),
    ),
    _Mutation(
        name="development_response_validation_removed",
        source="aletheia_lab/diagnosis/_development/runner.py",
        replacements=(
            (
                "                validate_response_against_authority(request, response, variant, case)\n",
                "                pass\n",
            ),
        ),
        target=(
            "tests/unit/test_diagnosis_development_runner.py::"
            "test_malformed_builtin_response_fails_without_terminal_publication"
        ),
    ),
    _Mutation(
        name="development_context_budget_understated",
        source="aletheia_lab/diagnosis/_development/resources.py",
        replacements=(
            (
                "        context_tokens_upper_bound=len(context_bytes),\n",
                "        context_tokens_upper_bound=(len(context_bytes) + 3) // 4,\n",
            ),
        ),
        target=(
            "tests/unit/test_diagnosis_development_runner.py::"
            "test_records_preserve_tool_ledgers_and_development_boundary"
        ),
    ),
)


def _apply_mutation(source_root: Path, mutation: _Mutation) -> None:
    path = source_root / mutation.source
    content = path.read_text(encoding="utf-8")
    for original, replacement in mutation.replacements:
        occurrences = content.count(original)
        if occurrences != 1:
            raise RuntimeError(
                f"mutation {mutation.name} expected one source match, found {occurrences}"
            )
        content = content.replace(original, replacement, 1)
    path.write_text(content, encoding="utf-8", newline="")


def _run_mutation(mutation: _Mutation) -> dict[str, str]:
    with tempfile.TemporaryDirectory(prefix="evaluation-mutation-") as temporary:
        overlay = Path(temporary) / "src"
        shutil.copytree(_ROOT / "src" / "aletheia_lab", overlay / "aletheia_lab")
        _apply_mutation(overlay, mutation)
        environment = dict(os.environ)
        existing_pythonpath = environment.get("PYTHONPATH")
        environment["PYTHONPATH"] = (
            os.fspath(overlay)
            if not existing_pythonpath
            else os.pathsep.join((os.fspath(overlay), existing_pythonpath))
        )
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-o",
                "pythonpath=",
                mutation.target,
                "-q",
            ],
            cwd=_ROOT,
            check=False,
            capture_output=True,
            text=True,
            env=environment,
            timeout=60,
        )
        if completed.returncode != 1:
            detail = (completed.stdout + completed.stderr)[-2000:]
            raise RuntimeError(
                f"mutation {mutation.name} was not detected by its target test "
                f"(return code {completed.returncode}):\n{detail}"
            )
    return {
        "mutation": mutation.name,
        "source": mutation.source,
        "target": mutation.target,
        "status": "detected",
    }


def main() -> int:
    results = [_run_mutation(mutation) for mutation in _MUTATIONS]
    evidence: dict[str, object] = {
        "schema_version": "evaluation-mutation-audit/v1",
        "results": results,
    }
    canonical = json.dumps(evidence, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    evidence["audit_sha256"] = hashlib.sha256(canonical.encode("ascii")).hexdigest()
    print(json.dumps(evidence, ensure_ascii=True, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
