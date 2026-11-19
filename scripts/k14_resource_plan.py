"""Print explicit fleet connection/admission bounds without resolving any secret."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from aletheia_lab.hosted.resource_budget import resource_budget
from aletheia_lab.hosted.settings import load_runtime_settings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--api-replicas', type=int, default=1)
    parser.add_argument('--worker-replicas', type=int, default=1)
    args = parser.parse_args()
    try:
        report = resource_budget(load_runtime_settings(args.config),
            api_replicas=args.api_replicas, worker_replicas=args.worker_replicas)
    except (OSError, ValueError):
        print(json.dumps({'error': 'resource_plan_invalid'}))
        return 1
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0 if report['within_database_budget'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
