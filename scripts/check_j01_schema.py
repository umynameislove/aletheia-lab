"""Independent draft-2020-12 validation; no server or deployment is contacted."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("fixtures", type=Path)
    root = parser.parse_args(argv).fixtures
    formats = FormatChecker()
    if "date-time" not in formats.checkers:
        raise RuntimeError("Install jsonschema[format] with its date-time checker before validating fixtures.")

    def load(name: str) -> dict:
        return json.loads((root / name).read_text(encoding="utf-8"))

    checked = 0
    for name in ("session", "capabilities", "job", "job-receipt", "page"):
        schema = load(f"{name}.schema.json")
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema, format_checker=formats).validate(load(f"{name}.json"))
        checked += 1

    for path in root.glob("session-needs-workspace-*.json"):
        Draft202012Validator(load("session.schema.json"), format_checker=formats).validate(load(path.name))
        checked += 1

    output_paths = sorted(root.glob("job-output-*.json"))
    if (root / "job-output.schema.json").exists():
        schema = load("job-output.schema.json")
        Draft202012Validator.check_schema(schema)
        output = Draft202012Validator(schema, format_checker=formats)
        for path in output_paths:
            value = load(path.name)
            output.validate(value)
            if value["operation"] != path.stem.removeprefix("job-output-"):
                raise ValueError("Job output operation does not match its filename")
            checked += 1
        example = {"schema_version": "product-api/v1-draft", "workspace_id": "ws_synthetic",
                   "job_id": "job_synthetic", "operation": "preview", "result": {}}
        output.validate(example)
        for patch in ({"extra": True}, {"schema_version": "product-api/v2"},
                      {"operation": "unknown"}, {"result": []}):
            if output.is_valid({**example, **patch}):
                raise ValueError("Invalid job output accepted")
    elif output_paths:
        raise ValueError("Job output payloads require their producer schema")

    producer = Draft202012Validator(load("problem-producer.schema.json"))
    consumer = Draft202012Validator(load("problem-consumer.schema.json"))
    for path in root.glob("problem-*.json"):
        if ".schema." in path.name:
            continue
        value = load(path.name)
        producer.validate(value)
        consumer.validate(value)
        checked += 1
        bad = {**value, "status": 500}
        if producer.is_valid(bad) or consumer.is_valid(bad):
            raise ValueError(f"mismatched code/status accepted: {path.name}")

    extended = {**load("problem-session-expired.json"), "instance": "/synthetic"}
    if producer.is_valid(extended) or not consumer.is_valid(extended):
        raise ValueError("producer/consumer extension policy differs from contract")
    session = Draft202012Validator(load("session.schema.json"), format_checker=formats)
    for expiry in ("soon", "2026-02-30T12:00:00Z", "2026-01-01", "2026-12-31T23:59:60Z", "0000-01-01T00:00:00Z"):
        bad = copy.deepcopy(load("session.json"))
        bad["session"]["expires_at"] = expiry
        if session.is_valid(bad):
            raise ValueError(f"invalid date-time accepted: {expiry}")
    page = Draft202012Validator(load("page.schema.json"))
    bad = {**load("page.json"), "total": 0, "total_kind": "unknown"}
    if page.is_valid(bad):
        raise ValueError("unknown total accepted a numeric value")
    bad = {**load("page.json"), "next_cursor": ""}
    if page.is_valid(bad):
        raise ValueError("empty cursor accepted")
    receipt = Draft202012Validator(load("job-receipt.schema.json"))
    bad = {**load("job-receipt.json"), "status_url": "https://attacker.invalid/jobs/1"}
    if receipt.is_valid(bad):
        raise ValueError("absolute status URL accepted")
    print(f"VALID_FIXTURES={checked}")
    print("INVALID_CODE_STATUS_REJECTED=True")
    print("INVALID_DATE_TIME_REJECTED=True")
    print("EXTENSION_POLICY_MATCH=True")
    print("TOTAL_KIND_CONSISTENT=True")
    print("EMPTY_CURSOR_AND_ABSOLUTE_URL_REJECTED=True")


if __name__ == "__main__":
    main()
