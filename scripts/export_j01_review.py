"""Export synthetic draft contracts to a new directory; never replace prior evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from aletheia_lab.hosted_contract.job_page import Job, JobOutput, JobReceipt, Page
from aletheia_lab.hosted_contract.session_capabilities import (
    Capabilities,
    Problem,
    Session,
    problem_consumer_schema,
)
from aletheia_lab.product.view import ProductView


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--observed-output-dir", type=Path)
    args = parser.parse_args(argv)
    fixtures = Path(__file__).resolve().parents[1] / "fixtures" / "j01"
    outputs: dict[str, object] = {}
    for name, model in (("session", Session), ("capabilities", Capabilities)):
        value = model.model_validate_json((fixtures / f"{name}-proposal.json").read_text(encoding="utf-8"))
        outputs[f"{name}.json"] = value.model_dump(mode="json")
        outputs[f"{name}.schema.json"] = model.model_json_schema()
        if name == "session":
            for allowed in (False, True):
                example = value.model_dump(mode="json")
                example.update(onboarding="needs_workspace", last_workspace_id=None, can_create_workspace=allowed)
                outputs[f"session-needs-workspace-create-{'allowed' if allowed else 'denied'}.json"] = Session.model_validate_json(json.dumps(example)).model_dump(mode="json")
    for name in ("session-expired", "session-revoked", "membership-revoked", "permission-denied", "csrf-invalid"):
        value = Problem.model_validate_json(
            (fixtures / f"problem-{name}-proposal.json").read_text(encoding="utf-8")
        )
        outputs[f"problem-{name}.json"] = value.model_dump(mode="json")
    for code, status in (
        ("idempotency_conflict", 409),
        ("cursor_invalid", 400),
        ("cursor_expired", 410),
        ("revision_conflict", 412),
        ("precondition_required", 428),
    ):
        value = Problem(
            schema_version="product-api/v1-draft",
            type=f"urn:aletheia:problem:{code.replace('_', '-')}",
            title="Request could not be completed",
            status=status,
            code=code,
            detail="The request could not be completed.",
            request_id="req_synthetic_001",
        )
        outputs[f"problem-{code.replace('_', '-')}.json"] = value.model_dump(mode="json")
    outputs["problem-producer.schema.json"] = Problem.model_json_schema()
    outputs["problem-consumer.schema.json"] = problem_consumer_schema()
    receipt = JobReceipt(
        schema_version="product-api/v1-draft",
        workspace_id="ws_synthetic_001",
        job_id="job_synthetic_001",
        status="queued",
        request_id="req_synthetic_001",
        status_url="/v1/workspaces/ws_synthetic_001/jobs/job_synthetic_001",
    )
    job = Job(
        schema_version="product-api/v1-draft",
        workspace_id=receipt.workspace_id,
        job_id=receipt.job_id,
        status="running",
        progress=None,
    )
    page = Page[dict[str, str]](
        schema_version="product-api/v1-draft",
        workspace_id=receipt.workspace_id,
        items=[{"id": "result_synthetic_001"}],
        next_cursor=None,
        total=None,
        total_kind="unknown",
    )
    for name, value in (("job-receipt", receipt), ("job", job), ("page", page)):
        outputs[f"{name}.json"] = value.model_dump(mode="json")
        outputs[f"{name}.schema.json"] = type(value).model_json_schema()
    output_model = JobOutput[dict[str, object]]
    outputs["job-output.schema.json"] = output_model.model_json_schema()
    provenance = ["Synthetic review fixtures; optional job outputs are serialized from supplied observations."]
    for source in (Path(__file__), *(Path(__file__).resolve().parents[1] / name for name in (
            "src/aletheia_lab/hosted_contract/job_page.py", "src/aletheia_lab/hosted_contract/session_capabilities.py",
            "src/aletheia_lab/product/view.py"))):
        provenance.append(f"source_sha256 {hashlib.sha256(source.read_bytes()).hexdigest()} {source.name}")
    if args.observed_output_dir is not None:
        observed = args.observed_output_dir
        report = json.loads((observed / "report.json").read_text(encoding="utf-8"))
        source = json.loads((observed / "source.json").read_text(encoding="utf-8"))
        jobs = {row["operation"]: row["job_id"] for row in report["jobs"]}
        for operation in ("preview", "confirm", "analyze"):
            path = observed / f"{operation}.json"
            raw = path.read_bytes()
            value = output_model.model_validate_json(raw)
            if (value.workspace_id != source["workspace_id"] or value.job_id != jobs[operation]
                    or value.operation != operation):
                raise ValueError("Observed job output scope does not match its run.")
            if operation == "analyze":
                view = ProductView.model_validate_json(json.dumps(value.result, allow_nan=False))
                if (view.demo_only or view.visibility != "diagnosis" or view.snapshot.id != report["snapshot_id"]
                        or view.result.id != report["result_id"]):
                    raise ValueError("Observed analysis does not match its hosted run.")
            outputs[f"job-output-{operation}.json"] = value.model_dump(mode="json")
            provenance.append(f"observed_sha256 {hashlib.sha256(raw).hexdigest()} {path.name}")
        for name in ("report.json", "source.json"):
            provenance.append(f"observed_sha256 {hashlib.sha256((observed / name).read_bytes()).hexdigest()} {name}")
    args.output.mkdir(parents=False, exist_ok=False)
    sums = []
    for name, value in outputs.items():
        data = (json.dumps(value, ensure_ascii=True, indent=2) + "\n").encode()
        with (args.output / name).open("xb") as target:
            target.write(data)
        digest = hashlib.sha256(data).hexdigest()
        sums.append(f"{digest}  {name}\n")
        print(name, digest)
    metadata = ("\n".join(provenance) + "\n").encode()
    with (args.output / "provenance.txt").open("xb") as target:
        target.write(metadata)
    sums.append(f"{hashlib.sha256(metadata).hexdigest()}  provenance.txt\n")
    with (args.output / "SHA256SUMS").open("x", encoding="ascii", newline="\n") as target:
        target.writelines(sums)


if __name__ == "__main__":
    main()
