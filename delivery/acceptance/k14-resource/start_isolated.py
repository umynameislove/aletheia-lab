"""Provision the existing K14 fixture under a fresh Docker ownership label."""

import importlib.util
import json
import subprocess
import tempfile
import time
from pathlib import Path
from uuid import uuid4

out = Path(__file__).resolve().parent
repo = Path.home() / ".codex/worktrees/product-api-publication/_repo_aletheia_ref"
source = repo / "delivery/kien-r418/evidence/k14-staging/state-public.json"
tools = repo / "delivery/kien-r418/staging/k14-local"
assert source.is_file() and (tools / "stage_up.py").is_file()

spec = importlib.util.spec_from_file_location("stage_up", tools / "stage_up.py")
stage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stage)
stage.OUT = out
stage.ROOT = repo

record = json.loads(source.read_text(encoding="utf-8"))
run = uuid4().hex[:12]
private = Path(tempfile.mkdtemp(prefix="k1401-" + run + "-", dir="C:/t"))
state = {key: record[key] for key in ("head", "image", "image_id", "release_identity")}
state.update(
    run_id=run,
    project="aletheia-k14-stg-" + run,
    phase="image-built",
    private_dir=str(private),
)
with (out / "state-public.json").open("x", encoding="utf-8") as stream:
    json.dump(state, stream, indent=2)

stage.main("dependencies")
for _ in range(45):
    ready = subprocess.run(
        [
            "docker",
            "exec",
            state["project"] + "-pg",
            "pg_isready",
            "-h",
            "127.0.0.1",
            "-d",
            "aletheia",
        ],
        capture_output=True,
        check=False,
        timeout=8,
    )
    if ready.returncode == 0:
        break
    time.sleep(2)
else:
    raise RuntimeError("Owned PostgreSQL did not become ready")
stage.main("start-runtime")
