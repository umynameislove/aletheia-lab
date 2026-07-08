"""Run the P1 eval-first vertical slice.

This script is intentionally small. The goal is to force an early end-to-end
loop before building the larger platform:

inject -> evidence -> diagnosis variants -> evaluation -> error analysis
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aletheia_lab.config import load_yaml


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("configs/project.yaml"))
    args = parser.parse_args()

    config = load_yaml(args.config)
    vertical_slice = config["vertical_slice"]
    payload = {
        "status": "planned",
        "phase": vertical_slice["phase"],
        "fault_type": vertical_slice["fault_type"],
        "target_cases": vertical_slice["target_cases"],
        "next_step": "implement deterministic data_drift injector and first 15-case manifest",
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
