"""Run the isolated measurement and preserve the raw Docker receipts."""

import hashlib
import json
import subprocess
from contextlib import ExitStack
from pathlib import Path

out = Path(__file__).resolve().parent
state = json.loads((out / "state-public.json").read_text(encoding="utf-8"))
run = state["run_id"]
project = "aletheia-k14-stg-" + run
assert run != "da2093353a73" and state["project"] == project
assert state["phase"] == "runtime-started"
tools = (
    Path.home()
    / ".codex/worktrees/product-api-publication/_repo_aletheia_ref/delivery/kien-r418/staging/k14-local"
)


def docker(*args):
    return subprocess.check_output(["docker", *args], timeout=20)


def check_owner(kind, identity):
    info = json.loads(docker(kind, "inspect", identity))[0]
    labels = info["Config"]["Labels"] if kind == "container" else info["Labels"]
    assert labels["com.aletheia.k14.owner"] == run, (kind, identity)
    return info


assert check_owner("network", state["network"])["Internal"]
quotas = {}
for component, identity in state["containers"].items():
    info = check_owner("container", identity)
    assert info["Name"] == f"/{project}-{component}" and info["State"]["Running"], (
        component
    )
    quotas[component] = {
        key: info["HostConfig"][key]
        for key in ("Memory", "NanoCpus", "PidsLimit", "Ulimits")
    }
(out / "quotas.json").write_text(json.dumps(quotas, indent=2), encoding="utf-8")

cmd = [
    "docker",
    "run",
    "--rm",
    "--pull=never",
    "-i",
    "--name",
    project + "-measure",
    "--label",
    "com.aletheia.k14.owner=" + run,
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
    "--env",
    "PYTHONPATH=/opt/k14",
    "--mount",
    f"type=bind,src={tools},dst=/opt/k14,readonly",
    "--entrypoint",
    "/opt/venv/bin/python",
]
for key, dst in (
    ("configs", "/run/config"),
    ("clients", "/run/load"),
    ("metrics", "/run/metrics"),
):
    volume = state["volumes"][key]
    check_owner("volume", volume)
    cmd += ["--mount", f"type=volume,src={volume},dst={dst},readonly"]

dataset = hashlib.sha256((out / "deployment-dataset.json").read_bytes()).hexdigest()
sampler = """
i=0
while [ "$i" -lt 500 ]; do
    if [ -r /sys/fs/cgroup/cpu.stat ]; then
        read -r _ cpu < /sys/fs/cgroup/cpu.stat
        read -r ram < /sys/fs/cgroup/memory.current
    else
        read -r cpu < /sys/fs/cgroup/cpuacct/cpuacct.usage
        cpu=$((cpu / 1000))
        read -r ram < /sys/fs/cgroup/memory/memory.usage_in_bytes
    fi
    printf '%s %s %s\\n' "$(date +%s.%N)" "$cpu" "$ram"
    i=$((i+1))
    sleep 0.5
done
"""
with (
    ExitStack() as stack,
    (out / "result.json").open("xb") as result,
    (out / "measure.log").open("xb") as log,
):
    monitors = []
    for role in ("pg", "idp"):
        output = stack.enter_context((out / (role + "-resource.tsv")).open("wb"))
        monitors.append(
            subprocess.Popen(
                ["docker", "exec", state["containers"][role], "sh", "-c", sampler],
                stdout=output,
                stderr=log,
            )
        )

    def stop_monitors():
        for monitor in monitors:
            monitor.terminate()
            monitor.wait(timeout=10)

    stack.callback(stop_monitors)
    subprocess.run(
        [*cmd, state["image_id"], "-B", "-", dataset],
        input=(out / "measure.py").read_bytes(),
        stdout=result,
        stderr=log,
        check=True,
        timeout=220,
    )
