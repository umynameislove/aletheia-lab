"""Compute agreement from two locked human submissions, without adjudicating them."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aletheia_lab.evaluation.serving_corpus_review import population_reliability, reliability
from aletheia_lab.filesystem import write_new_file


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--population-packet", type=Path)
    parser.add_argument("--first", type=Path, required=True)
    parser.add_argument("--second", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        values = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in (args.packet, args.first, args.second)
        ]
        if args.population_packet is None:
            result = reliability(*values)
        else:
            population = json.loads(args.population_packet.read_text(encoding="utf-8"))
            result = population_reliability(population, *values)
        write_new_file(args.output, (json.dumps(result, indent=2, sort_keys=True) + "\n").encode())
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(
            json.dumps(
                {"status": "human_coding_incomplete_or_invalid", "error_type": type(error).__name__}
            )
        )
        return 1
    print(
        json.dumps(
            {key: result[key] for key in ("packet_sha256", "primary_boundary", "source_tier")},
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
