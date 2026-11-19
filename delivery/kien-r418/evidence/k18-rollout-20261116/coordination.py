"""Bounded retries for Docker bind-mount sharing on Windows."""

import json
import time


def read_json(path):
    for attempt in range(30):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (PermissionError, FileNotFoundError):
            if attempt == 29:
                raise
            time.sleep(0.02)


def write_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
    for attempt in range(30):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            if attempt == 29:
                raise
            time.sleep(0.02)
