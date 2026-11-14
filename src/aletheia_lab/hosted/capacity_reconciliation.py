"""Bounded read-only lookup of ambiguous capacity mutations, without rewriting SLOs."""

from __future__ import annotations

import re
from typing import Any

from .operator_observation import _boundary


async def reconcile_mutations(conn: Any, schema: str, result: dict[str, Any]) -> dict[str, Any]:
    await _boundary(conn, schema)
    source = result.get("mutation_reconciliation", {})
    total, witnesses = source.get("unresolved"), source.get("witnesses")
    if (
        type(total) is not int
        or total < 0
        or not isinstance(witnesses, list)
        or len(witnesses) > 100
        or total < len(witnesses)
    ):
        raise ValueError("Bounded mutation witness counters required.")
    observations = []
    unsupported = 0
    for witness in witnesses:
        if not isinstance(witness, dict):
            raise ValueError("Known mutation witness required.")
        workspace, key, operation = (
            witness.get(n) for n in ("workspace_id", "request_key_sha256", "operation_id")
        )
        if (
            not isinstance(workspace, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", workspace) is None
            or not isinstance(key, str)
            or re.fullmatch(r"[0-9a-f]{64}", key) is None
        ):
            raise ValueError("Scoped opaque witness and key hash required.")
        if operation == "P23":
            rows = await (
                await conn.execute(
                    "SELECT export_id,object_sha256,result_id FROM k02_exports "
                    "WHERE workspace_id=%s AND request_key_hash=%s LIMIT 2",
                    (workspace, key),
                )
            ).fetchall()
            effects = [{"export_id": r[0], "object_sha256": r[1], "result_id": r[2]} for r in rows]
        elif operation in ("P07", "P09", "P10", "P13", "P19"):
            rows = await (
                await conn.execute(
                    "SELECT job_id,state,operation FROM j01_jobs "
                    "WHERE workspace_id=%s AND key_hash=%s LIMIT 2",
                    (workspace, key),
                )
            ).fetchall()
            effects = [{"job_id": r[0], "state": r[1], "operation": r[2]} for r in rows]
        else:
            unsupported += 1
            observations.append({**witness, "status": "operation_lookup_not_supported"})
            continue
        if len(effects) > 1:
            raise ValueError("Multiple durable effects for one generated scoped request key.")
        observations.append(
            {
                **witness,
                "status": "durable_effect_visible"
                if effects
                else "no_effect_visible_at_observation",
                "effects": effects,
            }
        )
    return {
        "schema_version": "k17-mutation-reconciliation/v1",
        "profile_sha256": result.get("profile_sha256"),
        "original_unresolved_mutations": total,
        "witnesses_observed": len(witnesses),
        "unobserved_or_unsupported": total - len(witnesses) + unsupported,
        "observations": observations,
        "original_slo_outcomes_unchanged": True,
        "scope": "Read-only point-in-time metadata. Absence is a final no-effect conclusion only with independently verified API/worker quiescence. Visible bytes still need full hash verification. Original timeout/503/rejection remains failed in its original deadline denominator.",
    }
