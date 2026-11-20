"""Run an evidence-producing client on the owned staging network."""

import json
import subprocess
import sys

from stage_up import OUT, TOOLS, docker

name = sys.argv[1]
assert name.isascii() and name.replace("-", "").isalnum()
script = "stage_burst.py" if name.startswith("burst-") else "stage_load.py"
state = json.loads((OUT / "state-public.json").read_text())
owner = state["run_id"]
network = json.loads(docker("network", "inspect", state["network"]))[0]
assert network["Internal"] and network["Labels"]["com.aletheia.k14.owner"] == owner
out = OUT / name
out.mkdir(exist_ok=False)
cmd = [
    "docker",
    "run",
    "--rm",
    "--pull=never",
    "--name",
    state["project"] + "-" + name,
    "--label",
    "com.aletheia.k14.owner=" + owner,
    "--network",
    state["network"],
    "--memory",
    "384m",
    "--cpus",
    "0.75",
    "--pids-limit",
    "64",
    "--ulimit",
    "nofile=256:256",
    "--read-only",
    "--cap-drop",
    "ALL",
    "--security-opt",
    "no-new-privileges:true",
    "--tmpfs",
    "/tmp:rw,size=67108864",
    "--mount",
    f"type=bind,src={TOOLS},dst=/opt/k14,readonly",
    "--mount",
    f"type=bind,src={OUT},dst=/evidence",
    "--entrypoint",
    "/opt/venv/bin/python",
]
for key, path in (
    ("configs", "/run/config"),
    ("clients", "/run/load"),
    ("metrics", "/run/metrics"),
):
    volume = state["volumes"][key]
    row = json.loads(docker("volume", "inspect", volume))[0]
    assert row["Labels"]["com.aletheia.k14.owner"] == owner
    cmd.extend(["--mount", f"type=volume,src={volume},dst={path},readonly"])
with (out / "console.log").open("wb") as log:
    run = subprocess.run(
        [
            *cmd,
            state["image_id"],
            "-B",
            "/opt/k14/" + script,
            f"/evidence/{name}/result.json",
            *sys.argv[2:],
        ],
        stdout=log,
        stderr=subprocess.STDOUT,
    )
(out / "exit.json").write_text(json.dumps({"exit_code": run.returncode}), encoding="utf-8")
print(name, "exit", run.returncode, flush=True)
raise SystemExit(run.returncode)
