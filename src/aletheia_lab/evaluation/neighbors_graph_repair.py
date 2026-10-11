"""Forward numerical-closure repair check, not a new upstream bug claim.

The retained counterexample suggests that identity compliance cannot certify
functional graph coverage. The repair fills the precomputed graph; it does not
repair missing capture or retrospectively identify an uncaptured request.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

from aletheia_lab.evaluation.neighbors_audit_transfer import array


def check(seed: int, full: bool) -> dict[str, Any]:
    if seed not in (59, 62, 67) or type(full) is not bool:
        raise ValueError("outside prospectively fixed repair slice")
    np = import_module("numpy")
    neighbors = import_module("sklearn.neighbors")
    pipeline = import_module("sklearn.pipeline")
    rng = np.random.RandomState(seed)
    x, query, targets = 2 * rng.rand(40, 5) - 1, 2 * rng.rand(40, 5) - 1, rng.rand(40, 1)
    # distance-mode transformer includes an extra edge; 39 requests all 40 rows.
    transform = neighbors.KNeighborsTransformer(n_neighbors=39 if full else 24, mode="distance")
    regressor = neighbors.RadiusNeighborsRegressor(radius=1.5, metric="precomputed")
    chain = pipeline.make_pipeline(transform, regressor)
    observed = chain.fit(x, targets).predict(query)
    reference = neighbors.RadiusNeighborsRegressor(radius=1.5).fit(x, targets).predict(query)
    graph = transform.transform(query)  # Separate inspection, counted explicitly.
    distances = np.linalg.norm(query[:, None, :] - x[None, :, :], axis=2)
    missing = []
    for ordinal in range(40):
        eligible = set(np.flatnonzero(distances[ordinal] <= 1.5).tolist())
        present = set(graph.indices[graph.indptr[ordinal] : graph.indptr[ordinal + 1]].tolist())
        if eligible - present:
            missing.append(
                {"row": ordinal, "eligible": len(eligible), "absent": sorted(eligible - present)}
            )
    return {
        "seed": seed,
        "repair": "full_graph" if full else "source_factor_two",
        "phase": "development after failure" if seed == 59 else "prospective unused data",
        "observed": array(observed),
        "reference": array(reference),
        "missing_radius_neighbors": missing,
        "equivalent": bool(np.allclose(observed, reference, equal_nan=True)),
        "graph_nnz": int(graph.nnz),
        "native_predictions": 2,
        "additional_inspection_transforms": 1,
        "scope": "authored source-informed numerical repair; not capture repair",
    }
