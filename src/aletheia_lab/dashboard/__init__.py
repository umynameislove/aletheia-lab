"""Local transport that lets the P6 web dashboard call the in-process ProductService."""

from aletheia_lab.dashboard.bridge import DashboardServer, dispatch, load_service

__all__ = ["DashboardServer", "dispatch", "load_service"]
