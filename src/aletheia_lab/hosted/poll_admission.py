"""Only in-flight poll scopes are stored; a noisy workspace cannot occupy all slots."""
from __future__ import annotations

from .resource_admission import ImmediateSlots, ResourceBusy


class PollAdmission:
    def __init__(self, total: int = 16, per_workspace: int = 4) -> None:
        if type(per_workspace) is not int or not 1 <= per_workspace <= total:
            raise ValueError('A bounded workspace poll limit is required.')
        self.slots = ImmediateSlots(total)
        self.per_workspace = per_workspace
        self._workspaces: dict[str, int] = {}

    def acquire(self, workspace: str) -> None:
        self.slots.acquire()
        if self._workspaces.get(workspace, 0) >= self.per_workspace:
            self.slots.release()
            raise ResourceBusy('Workspace poll capacity is temporarily unavailable.')
        self._workspaces[workspace] = self._workspaces.get(workspace, 0) + 1

    def release(self, workspace: str) -> None:
        if self._workspaces.get(workspace, 0) < 1:
            raise RuntimeError('Only an acquired poll scope can be released.')
        count = self._workspaces[workspace] - 1
        if count:
            self._workspaces[workspace] = count
        else:
            del self._workspaces[workspace]
        self.slots.release()

    @property
    def active_workspaces(self) -> int:
        return len(self._workspaces)
