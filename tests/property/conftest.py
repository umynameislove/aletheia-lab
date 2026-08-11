"""Hypothesis configuration for the property-based test suite.

Scoped to tests/property/; loaded only when this directory is collected.

The profile keeps generated examples reproducible and the worktree clean:

- database=None: prevents .hypothesis/ from appearing in the worktree.
  No filesystem residue from property test runs.

- derandomize=True: all generated examples are deterministic given a fixed
  Hypothesis version and seed.  CI runs are therefore reproducible without
  pinning a specific corpus.

- max_examples=100: baseline for pure contract and hash invariants.
  Costlier intervention tests override to max_examples=40 per test with
  @settings(max_examples=40) where the fixture cost is significantly higher.

- No broad suppress_health_check at the profile level.  Individual tests
  may suppress a *specific* HealthCheck entry, must do so narrowly, and
  must include an inline comment explaining the concrete reason.
"""

from __future__ import annotations

from hypothesis import settings

# ---------------------------------------------------------------------------
# Profile: deterministic, no filesystem residue, adequate example count.
# ---------------------------------------------------------------------------
settings.register_profile(
    "property_ci",
    max_examples=100,
    database=None,
    derandomize=True,
)
settings.load_profile("property_ci")
