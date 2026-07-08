"""Plot helpers.

Keep plotting code separate from metric computation so the paper tables remain
reproducible without the dashboard.
"""

from __future__ import annotations


def figure_filename(metric_name: str, extension: str = "png") -> str:
    """Return a normalized figure filename."""

    safe_name = metric_name.strip().casefold().replace(" ", "_").replace("/", "_")
    return f"{safe_name}.{extension}"
