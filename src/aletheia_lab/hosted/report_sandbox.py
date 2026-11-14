"""Limits for the one-shot trusted report child, never applied to the API process."""
from __future__ import annotations

import os
import sys


def restrict_report_process() -> None:
    for name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ[name] = "1"
    if sys.platform != "win32":
        import resource

        limits = [(resource.RLIMIT_CPU, 15, 16), (resource.RLIMIT_NOFILE, 64, 64),
                  (resource.RLIMIT_FSIZE, 32 * 1024 * 1024, 32 * 1024 * 1024)]
        # macOS reserves large virtual mappings; RLIMIT_RSS there is not enforced.
        if sys.platform.startswith("linux"):
            limits.append((resource.RLIMIT_AS, 1024 * 1024 * 1024, 1024 * 1024 * 1024))
        for kind, soft, hard in limits:
            old_soft, old_hard = resource.getrlimit(kind)
            hard = min(hard, old_hard) if old_hard != resource.RLIM_INFINITY else hard
            soft = min(soft, hard, old_soft) if old_soft != resource.RLIM_INFINITY else min(soft, hard)
            resource.setrlimit(kind, (soft, hard))

    def deny_external_effects(event: str, args: tuple[object, ...]) -> None:
        if event.startswith("socket.") or event in ("subprocess.Popen", "os.system", "os.posix_spawn"):
            raise PermissionError("Report processes cannot use network or start programs.")

    sys.addaudithook(deny_external_effects)
