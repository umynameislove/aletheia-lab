#!/usr/bin/env python3
"""Verify the outcome-blind claim-normalization recovery registration."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aletheia_lab.evaluation.claim_corpus_normalization_recovery import (
    ClaimCorpusNormalizationRecoveryError,
    load_recovery_protocol,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("verify",))
    parser.add_argument("--root", type=Path, default=Path("."))
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        protocol = load_recovery_protocol(args.root.resolve())
    except (ClaimCorpusNormalizationRecoveryError, OSError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "status": "normalization_recovery_registration_invalid",
                    "error": str(exc),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 2
    print(
        json.dumps(
            {
                "status": protocol.status,
                "protocol_sha256": protocol.protocol_sha256,
                "provider_output_schema_version": (protocol.provider_output_schema_version),
                "response_schema_set_sha256": (protocol.response_schema_set_sha256),
                "predecessor_terminal_request_count": (protocol.predecessor_terminal_request_count),
                "predecessor_normalized_output_count": (
                    protocol.predecessor_normalized_output_count
                ),
                "predecessor_schema_rejection_count": (protocol.predecessor_schema_rejection_count),
                "predecessor_technical_failure_count": (
                    protocol.predecessor_technical_failure_count
                ),
                "predecessor_claim_candidate_count": (protocol.predecessor_claim_candidate_count),
                "target_claim_count": protocol.target_claim_count,
                "registered_recovery_attempts": (protocol.registered_recovery_attempts),
                "new_authorization_required": protocol.new_authorization_required,
                "provider_calls_executed": protocol.provider_calls_executed,
                "claims_materialized": protocol.claims_materialized,
                "automatic_labels_generated": (protocol.automatic_labels_generated),
                "blind_packets_generated": protocol.blind_packets_generated,
                "human_annotations_collected": (protocol.human_annotations_collected),
                "main_or_sealed_outcomes_opened": (protocol.main_or_sealed_outcomes_opened),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
