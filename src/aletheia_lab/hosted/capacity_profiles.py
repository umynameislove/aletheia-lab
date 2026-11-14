"""Explicit calibration journeys; reference/primary campaigns retain separate open gates."""

from __future__ import annotations

from typing import Literal

from .application import VERSION
from .capacity_models import ActionBudget, Assertion, Disposition, Journey, Profile, Role, Step

_ALL: tuple[Role, ...] = ("owner", "admin", "analyst", "reviewer", "viewer")
_WRITE: tuple[Role, ...] = ("owner", "admin", "analyst")
_W = "/v1/workspaces/{workspace_id}"
_R = _W + "/results/{result_id}"


def eq(pointer: str, value: object) -> Assertion:
    return Assertion(pointer=pointer, check="equals", value=value)


def calibration_profile(
    *,
    target: str,
    origin: str,
    host: str,
    release_sha256: str,
    dataset_sha256: str,
    mode: Literal["closed", "open"] = "closed",
    run_label: str = "calibration",
    tls_ca_file: str | None = None,
) -> Profile:
    """Pinned starting assumptions, not an observed-user profile or primary50k fixture."""
    scope = (
        eq("/workspace_id", "$workspace_id"),
        eq("/project_id", "$project_id"),
        eq("/result_id", "$result_id"),
    )
    journeys: tuple[Journey, ...] = (
        Journey(
            name="result_header",
            weight=40,
            roles=_ALL,
            deadline_seconds=0.5,
            steps=(
                Step(
                    operation_id="P15",
                    path=_R + "/header",
                    assertions=scope + (eq("/snapshot_id", "$snapshot_id"),),
                ),
            ),
        ),
        Journey(
            name="claim_citation",
            weight=20,
            roles=_ALL,
            deadline_seconds=1.5,
            steps=(
                Step(
                    operation_id="P16",
                    path=_R + "/claims?limit=50",
                    assertions=scope
                    + (
                        Assertion(pointer="/items", check="nonempty"),
                        Assertion(pointer="/items", check="length_le", value=50),
                    ),
                    captures={
                        "selected_claim": "/items/0/id",
                        "selected_evidence": "/items/0/citation_ids/0",
                    },
                ),
                Step(
                    operation_id="P17",
                    path=_R + "/evidence/{selected_evidence}?claim_id={selected_claim}",
                    assertions=scope
                    + (
                        eq("/claim_id", "$selected_claim"),
                        eq("/evidence_id", "$selected_evidence"),
                        eq("/relation", "citation"),
                        Assertion(pointer="/evidence/text", check="nonempty"),
                    ),
                ),
            ),
        ),
        Journey(
            name="project_current",
            weight=10,
            roles=_ALL,
            deadline_seconds=0.5,
            steps=(
                Step(
                    operation_id="P11",
                    path=_W + "/projects/{project_id}",
                    assertions=(
                        eq("/workspace_id", "$workspace_id"),
                        eq("/project_id", "$project_id"),
                    ),
                ),
            ),
        ),
        Journey(
            name="graph_page",
            weight=10,
            roles=_ALL,
            deadline_seconds=1.0,
            steps=(
                Step(
                    operation_id="P18",
                    path=_R + "/graph?limit=100",
                    assertions=scope
                    + (
                        Assertion(pointer="/graph/nodes", check="nonempty"),
                        Assertion(pointer="/graph/nodes", check="length_le", value=100),
                    ),
                ),
            ),
        ),
        Journey(
            name="analysis_terminal",
            weight=5,
            roles=_WRITE,
            deadline_seconds=120.0,
            steps=(
                Step(
                    operation_id="P13",
                    method="POST",
                    path=_W + "/jobs",
                    statuses=(202,),
                    body={
                        "operation": "analyze",
                        "snapshot_id": "$snapshot_id",
                        "question": "$action_question",
                    },
                    follow_job=True,
                    assertions=(
                        eq("/workspace_id", "$workspace_id"),
                        eq("/operation", "analyze"),
                        eq("/result/project/id", "$project_id"),
                        eq("/result/snapshot/id", "$snapshot_id"),
                        Assertion(pointer="/result/claims", check="nonempty"),
                    ),
                ),
            ),
        ),
        Journey(
            name="followup_terminal",
            weight=3,
            roles=_WRITE,
            deadline_seconds=120.0,
            steps=(
                Step(
                    operation_id="P11",
                    path=_W + "/projects/{project_id}",
                    assertions=(eq("/project_id", "$project_id"),),
                    captures={"project_revision": "/revision"},
                ),
                Step(
                    operation_id="P19",
                    method="POST",
                    path=_R + "/follow-ups",
                    statuses=(202,),
                    headers={"if-match": "$project_revision"},
                    body={
                        "schema_version": VERSION,
                        "selection_kind": "claim",
                        "selection_id": "$claim_id",
                        "question": "$action_question",
                    },
                    follow_job=True,
                    assertions=(
                        eq("/workspace_id", "$workspace_id"),
                        eq("/operation", "follow_up"),
                        eq("/result/project_id", "$project_id"),
                        eq("/result/snapshot_id", "$snapshot_id"),
                        eq("/result/parent_result_id", "$result_id"),
                        Assertion(pointer="/result/result_id", check="nonempty"),
                    ),
                ),
            ),
        ),
        Journey(
            name="export_whole_file",
            weight=10,
            roles=_ALL,
            deadline_seconds=10.0,
            steps=(
                Step(
                    operation_id="P23",
                    method="POST",
                    path=_R + "/exports",
                    statuses=(201,),
                    body={"format": "json"},
                    assertions=scope,
                    download_descriptor=True,
                ),
            ),
        ),
        Journey(
            name="foreign_result_denied",
            weight=2,
            roles=_ALL,
            deadline_seconds=0.5,
            expected="deny",
            steps=(
                Step(
                    operation_id="P15",
                    path="/v1/workspaces/{foreign_workspace_id}/results/{foreign_result_id}/header",
                    statuses=(404,),
                    assertions=(
                        eq("/code", "not_found"),
                        eq("/type", "urn:aletheia:problem:not-found"),
                        eq("/title", "Request could not be completed"),
                        eq("/detail", "The requested resource is unavailable."),
                    ),
                ),
            ),
        ),
    )
    # These are protocol action budgets, not browser click-to-render observations.
    adjusted = []
    for journey in journeys:
        times = (
            {"S": (2.0, 5.0), "M": (10.0, 20.0), "L": (60.0, 120.0)}
            if journey.name in ("analysis_terminal", "followup_terminal")
            else {
                size: (
                    (
                        5.0
                        if journey.name == "export_whole_file"
                        else 0.7
                        if journey.name == "claim_citation"
                        else 0.4
                        if journey.name == "graph_page"
                        else 0.2
                    ),
                    journey.deadline_seconds,
                )
                for size in ("S", "M", "L")
            }
        )
        adjusted.append(
            journey.model_copy(
                update={
                    "size_budgets": {
                        size: ActionBudget(p95_seconds=p95, p99_seconds=p99, deadline_seconds=p99)
                        for size, (p95, p99) in times.items()
                    }
                }
            )
        )
    journeys = tuple(adjusted)
    covered = {s.operation_id for j in journeys for s in j.steps} | {"P20", "P24"}
    dispositions: dict[str, Disposition] = {
        f"P{n:02}": "recipe"
        if f"P{n:02}" in covered - {"P20", "P24"}
        else "dedicated"
        if f"P{n:02}" in covered
        else "not_run"
        for n in range(1, 39)
    }
    return Profile(
        target=target,
        origin=origin,
        host=host,
        tls_ca_file=tls_ca_file,
        release_sha256=release_sha256,
        dataset_sha256=dataset_sha256,
        seed=170013,
        mode=mode,
        duration_seconds=30.0,
        warmup_seconds=0.0,
        think_seconds=1.0,
        max_inflight=32,
        max_pending=128,
        arrivals_per_second=20.0,
        poll_seconds=0.5,
        job_polling="updates",
        operation_dispositions=dispositions,
        journeys=journeys,
        run_label=run_label,
    )


def expanded_profile(profile: Profile) -> Profile:
    """Executable browser/API reads, preview leases and own review; still not a primary campaign."""
    workspace = (eq("/workspace_id", "$workspace_id"),)
    project = workspace + (eq("/project_id", "$project_id"),)
    page = (Assertion(pointer="/items", check="nonempty"),
            Assertion(pointer="/items", check="length_le", value=50))
    reads: tuple[tuple[str, str, str, tuple[Role, ...], tuple[Assertion, ...]], ...] = (
        ("session_context", "P01", "/v1/session", _ALL,
         (eq("/actor/user_id", "$actor_id"), eq("/session/context_id", "$context_id"))),
        ("workspace_directory", "P03", "/v1/workspaces?limit=50", _ALL, page),
        ("workspace_capabilities", "P04", _W + "/capabilities", _ALL,
         workspace + (eq("/deployment_mode", "hosted"),)),
        ("project_page", "P05", _W + "/projects?limit=50", _ALL, workspace + page),
        ("snapshot_page", "P12", _W + "/projects/{project_id}/snapshots?limit=50", _ALL,
         project + page),
        ("result_catalog", "P14", _W + "/results?project_id={project_id}&snapshot_id={snapshot_id}&limit=50",
         _ALL, project + page),
        ("member_page", "P27", _W + "/members?limit=50", ("owner", "admin"), workspace + page),
        ("settings_read", "P37", _W + "/settings", ("owner", "admin"),
         workspace + (eq("/source_policy", "zip_bundle_only"),)),
    )
    additions = tuple(Journey(name=name, weight=5, roles=roles, deadline_seconds=0.5,
        steps=(Step(operation_id=operation, path=path, assertions=assertions),))
        for name, operation, path, roles, assertions in reads)
    additions += (
        Journey(name="preview_lease", weight=3, roles=_WRITE, deadline_seconds=120.0, steps=(
            Step(operation_id="P07", method="POST", path=_W + "/imports/preview", statuses=(202,),
                body={"schema_version": VERSION, "source_id": "$source_id"}, follow_job=True,
                assertions=workspace + (eq("/operation", "preview"),
                    eq("/result/source_id", "$source_id"), eq("/result/source_sha256", "$source_sha256")),
                captures={"preview_id": "/result/preview_id"}),
            Step(operation_id="P08", path=_W + "/imports/{preview_id}",
                assertions=workspace + (eq("/preview_id", "$preview_id"), eq("/source_id", "$source_id"),
                    eq("/source_sha256", "$source_sha256"))),
        )),
        Journey(name="own_review_comment", weight=2, roles=("owner", "admin", "analyst", "reviewer"),
            deadline_seconds=2.0, steps=(Step(operation_id="P26", method="PUT",
                path=_R + "/annotations/{action_id}", headers={"if-match": '"0"'},
                body={"body": "$action_question", "status": "comment"},
                assertions=project + (eq("/result_id", "$result_id"), eq("/annotation_id", "$action_id"),
                    eq("/author_id", "$actor_id"), eq("/revision", 1), eq("/status", "comment"))),
                Step(operation_id="P26", path=_R + "/annotations/{action_id}/history?limit=50",
                    assertions=project + (eq("/result_id", "$result_id"), eq("/annotation_id", "$action_id"),
                        eq("/items/0/author_id", "$actor_id"), eq("/items/0/revision", 1),
                        eq("/items/0/body", "$action_question"), eq("/items/0/status", "comment"))),)),
    )
    journeys = profile.journeys + additions
    dispositions = dict(profile.operation_dispositions)
    for journey in additions:
        for step in journey.steps:
            dispositions[step.operation_id] = "recipe"
    # P22 permits bounded job polling instead of SSE; _follow exercises that protocol.
    dispositions["P22"] = "dedicated"
    for operation in ("P32", "P33", "P34"):
        dispositions[operation] = "unsupported"
    return Profile.model_validate({**profile.model_dump(), "journeys": journeys,
                                   "operation_dispositions": dispositions})
