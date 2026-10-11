"""Read-only numerical, crypto and receipt checks of large-ONNX development."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from aletheia_lab.evaluation.serving_large_model_review import verify_large_development


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study-dir", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = verify_large_development(args.study_dir)
    except (ValueError, OSError, KeyError, TypeError, sqlite3.Error, ImportError) as error:
        print(
            json.dumps(
                {
                    "status": "large_development_review_failed_closed",
                    "error_type": type(error).__name__,
                }
            )
        )
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
