"""Bounded genuine ZIP sources and native worker-derived snapshot/result facts."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import Any

from aletheia_lab.hosted.content_hashing import content_hasher
from aletheia_lab.product.demo_flow import _demo_mapping

from .capacity_seed_models import SeedPlan


def source_payload(plan: SeedPlan, project: int, generation: int) -> bytes:
    size = plan.size(project)
    buffer = io.BytesIO()
    # Fixed ZIP metadata gives repeatable source bytes for the same pinned plan.
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        dataset = io.StringIO()
        dataset.write("id,target,feature\n")
        for row in range(plan.source_rows[size]):
            feature = content_hasher(
                f"{plan.seed}:{project}:{generation}:{row}".encode()
            ).hexdigest()
            dataset.write(f"{row},{row % 2},{feature}\n")
        metrics = ["run,name,value"]
        for index in range(plan.metric_series[size]):
            for run, offset in [("baseline", 0), ("candidate", 1)]:
                metrics.append(
                    f"{run},loss_{index},{(5000 + index + generation * 10 + offset * 100) / 10000:.4f}"
                )
        config = json.dumps(
            {
                "seed": plan.seed,
                "project": project,
                "generation": generation,
                "synthetic_capacity_fixture": True,
            },
            sort_keys=True,
        )
        for name, content in [
            ("dataset.csv", dataset.getvalue()),
            ("metrics.csv", "\n".join(metrics) + "\n"),
            ("config.json", config + "\n"),
        ]:
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            archive.writestr(info, content.encode())
    return buffer.getvalue()


def confirm_mapping(preview: dict[str, object], series: int) -> dict[str, object]:
    mapping = _demo_mapping(preview)
    mapping["metric_definitions"] = [
        {
            "metric_name": f"loss_{index}",
            "direction": "lower_is_better",
            "regression_threshold": 0.08,
        }
        for index in range(series)
    ]
    return mapping


def owned_scratch(root: Path) -> Path:
    root.mkdir(mode=0o700)
    return root.resolve(strict=True)


def _record_result_dimensions(view: Any, expected_claims: int, prepared_result: Any,
                              dimensions: dict[str, Any]) -> tuple[int, int]:
    from .capacity_seed import SeedRejected

    actual_claims = len(view.claims)
    if actual_claims != expected_claims:
        raise SeedRejected()
    dimensions["actual_graph_nodes_per_result"] = len(view.graph.nodes)
    dimensions["actual_graph_edges_per_result"] = len(view.graph.edges)
    count = prepared_result.staged.descriptor.byte_size
    dimensions["minimum_result_json_bytes"] = min(
        dimensions["minimum_result_json_bytes"] or count, count
    )
    dimensions["maximum_result_json_bytes"] = max(
        dimensions["maximum_result_json_bytes"], count
    )
    return actual_claims, count

