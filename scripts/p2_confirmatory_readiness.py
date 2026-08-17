"""Run a synthetic-only readiness check for confirmatory label-noise execution."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from aletheia_lab.benchmark.p2.confirmatory_execution import (
    ConfirmatoryExecutionError,
    ConfirmatoryTrainingSource,
    PredictionRole,
    ProbabilityVector,
    apply_class_conditional_noise,
    build_confirmatory_split,
    mutation_spec_for,
    repair_labels,
    score_probabilities,
    serialization_roundtrip,
    symmetric_matched_count,
    validate_complete_target_controls,
)
from aletheia_lab.benchmark.p2.confirmatory_inference import (
    holm_adjust,
    paired_sign_flip_pvalue,
    two_way_product_weight_bootstrap,
)
from aletheia_lab.benchmark.p2.confirmatory_protocol import (
    DEFAULT_CONFIRMATORY_PROTOCOL_PATH,
    ConfirmatoryProtocol,
    load_confirmatory_protocol,
    verify_confirmatory_predecessor,
)

_SHA = "a" * 64


def _git(root: Path, *arguments: str) -> str:
    try:
        completed = subprocess.run(
            ("git", "-C", str(root), *arguments),
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ConfirmatoryExecutionError("cannot verify the local protocol registration") from exc
    return completed.stdout.strip()


def _verify_local_tag(root: Path, protocol: ConfirmatoryProtocol) -> str:
    tag = protocol.governance.required_git_tag
    if _git(root, "cat-file", "-t", f"refs/tags/{tag}") != "tag":
        raise ConfirmatoryExecutionError("the required registration must be an annotated tag")
    tagged_payload = _git(
        root,
        "show",
        f"{tag}:configs/benchmark/p2_label_noise_confirmatory_protocol.json",
    )
    tagged_protocol = ConfirmatoryProtocol.model_validate_json(tagged_payload)
    if tagged_protocol.canonical_sha256() != protocol.canonical_sha256():
        raise ConfirmatoryExecutionError("the registration tag contains another protocol")
    tagged_commit = _git(root, "rev-parse", f"{tag}^{{}}")
    try:
        subprocess.run(
            ("git", "-C", str(root), "merge-base", "--is-ancestor", tagged_commit, "HEAD"),
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ConfirmatoryExecutionError(
            "the registered protocol is not an ancestor of HEAD"
        ) from exc
    return tagged_commit


def _synthetic_checks(protocol: ConfirmatoryProtocol) -> dict[str, object]:
    dataset = protocol.datasets[0]
    record_ids = tuple(f"readiness-{index:03d}" for index in range(100))
    labels = tuple(index % 2 for index in range(100))
    split = build_confirmatory_split(
        protocol=protocol,
        dataset=dataset,
        dataset_sha256=dataset.snapshot_sha256,
        record_ids=record_ids,
        labels=labels,
    )
    label_by_id = dict(zip(record_ids, labels, strict=True))
    train_ids = split.membership.train
    source = ConfirmatoryTrainingSource(
        dataset_id=dataset.dataset_id,
        dataset_role=dataset.role,
        record_ids=train_ids,
        clean_targets=tuple(label_by_id[record_id] for record_id in train_ids),
        dataset_sha256=dataset.snapshot_sha256,
        split_manifest_sha256=split.manifest.canonical_sha256(),
        feature_matrix_sha256=_SHA,
        preprocessing_sha256=_SHA,
        model_specification_sha256=_SHA,
        protocol_sha256=protocol.canonical_sha256(),
    )
    cell = next(item for item in protocol.intervention_cells if item.cell_id == "ccn-yes-to-no-30")
    spec = mutation_spec_for(
        protocol=protocol,
        dataset=dataset,
        cell=cell,
        seed=cell.primary_replicate_seeds[0],
    )
    mutation = apply_class_conditional_noise(source=source, spec=spec)
    controls = (
        serialization_roundtrip(source),
        symmetric_matched_count(source=source, mutation=mutation),
        repair_labels(source=source, mutation=mutation),
    )
    validate_complete_target_controls({control.control_id: control for control in controls})
    test_ids = split.membership.development
    truth = tuple(label_by_id[record_id] for record_id in test_ids)

    def vector(role: PredictionRole, probabilities: tuple[float, ...]) -> ProbabilityVector:
        return ProbabilityVector(
            role=role,
            record_ids=test_ids,
            positive_probabilities=probabilities,
            model_artifact_sha256=_SHA,
            training_targets_sha256=_SHA,
            evaluation_feature_matrix_sha256=_SHA,
            split_manifest_sha256=split.manifest.canonical_sha256(),
            protocol_sha256=protocol.canonical_sha256(),
        )

    clean_probabilities = tuple(0.9 if label else 0.1 for label in truth)
    observed_probabilities = tuple(0.65 if label else 0.1 for label in truth)
    clean_metrics = score_probabilities(
        true_labels=truth, vector=vector("clean_reference", clean_probabilities)
    )
    observed_metrics = score_probabilities(
        true_labels=truth,
        vector=vector("class_conditional", observed_probabilities),
    )
    interval = two_way_product_weight_bootstrap(
        clean_losses=clean_metrics.per_record_log_loss,
        observed_losses_by_seed=(
            observed_metrics.per_record_log_loss,
            tuple(value * 1.01 for value in observed_metrics.per_record_log_loss),
        ),
        resamples=100,
        seed=protocol.inference.bootstrap_seed,
    )
    pvalue = paired_sign_flip_pvalue(
        (0.2, 0.21, 0.22, 0.23),
        resamples=100,
        seed=protocol.inference.hypothesis_test_seed,
    )
    adjusted = holm_adjust({"yes_to_no": pvalue, "no_to_yes": min(1.0, pvalue * 2.0)})
    return {
        "split_manifest_sha256": split.manifest.canonical_sha256(),
        "mutation_sha256": mutation.canonical_sha256(),
        "nonreference_target_controls_passed": len(controls),
        "required_control_count": 4,
        "probability_metrics_reconciled": True,
        "two_way_bootstrap_exercised": interval.factors == ("evaluation_record", "corruption_seed"),
        "sign_flip_and_holm_exercised": set(adjusted) == {"yes_to_no", "no_to_yes"},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--protocol", type=Path, default=DEFAULT_CONFIRMATORY_PROTOCOL_PATH)
    args = parser.parse_args()
    root = args.root.resolve()
    protocol_path = args.protocol
    if not protocol_path.is_absolute():
        protocol_path = root / protocol_path
    protocol = load_confirmatory_protocol(protocol_path)
    verify_confirmatory_predecessor(protocol, root=root)
    tagged_commit = _verify_local_tag(root, protocol)
    report = {
        "status": "synthetic_ready",
        "protocol_sha256": protocol.canonical_sha256(),
        "required_git_tag": protocol.governance.required_git_tag,
        "tagged_protocol_commit": tagged_commit,
        "outcomes_generated": False,
        "sealed_test_opened": False,
        **_synthetic_checks(protocol),
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
