"""Verify the outcome-blind P2R instrument-validity protocol."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from aletheia_lab.benchmark.p2.instrument_validity import (
    load_instrument_validity_protocol,
    verify_instrument_validity_protocol,
)
from aletheia_lab.benchmark.p2.lightweight_protocol import (
    DEFAULT_DATA_DRIFT_PROTOCOL_PATH,
    DEFAULT_PREPROCESSING_PROTOCOL_PATH,
    load_lightweight_confirmatory_protocol,
    verify_protocol_pair,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("verify",))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "verify":
        protocol = verify_instrument_validity_protocol(load_instrument_validity_protocol())
        drift, preprocessing = verify_protocol_pair(
            load_lightweight_confirmatory_protocol(DEFAULT_DATA_DRIFT_PROTOCOL_PATH),
            load_lightweight_confirmatory_protocol(DEFAULT_PREPROCESSING_PROTOCOL_PATH),
        )
        print(
            json.dumps(
                {
                    "confirmatory_outcomes_generated": False,
                    "empty_evidence_visible_artifact_count": (
                        protocol.empty_evidence_negative_control.diagnosis_visible_artifact_count
                    ),
                    "model_fitted": False,
                    "mechanism_protocols": {
                        drift.mechanism: {
                            "protocol_sha256": drift.canonical_sha256(),
                            "required_git_tag": drift.required_git_tag,
                        },
                        preprocessing.mechanism: {
                            "protocol_sha256": preprocessing.canonical_sha256(),
                            "required_git_tag": preprocessing.required_git_tag,
                        },
                    },
                    "protocol_sha256": protocol.canonical_sha256(),
                    "required_mechanisms": list(protocol.required_mechanisms),
                    "status": "p2r_instrument_validity_protocol_verified_outcome_blind",
                },
                indent=2,
                sort_keys=True,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
