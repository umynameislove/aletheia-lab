"""Replace one replica at a time, preserving the earlier rollback image and receipts."""

import json
import subprocess
import sys
import time
from pathlib import Path

from coordination import read_json, write_json

ROOT = Path(__file__).resolve().parent
STATE = json.loads((ROOT / "state-public.json").read_text(encoding="utf-8"))
IMAGES = json.loads((ROOT / "images.json").read_text(encoding="utf-8"))
RUN = STATE["run_id"]
PROJECT = STATE["project"]
TOOLS = (
    Path.home()
    / ".codex/worktrees/product-api-publication/_repo_aletheia_ref/delivery/kien-r418/staging/k14-local"
)
assert STATE["phase"] == "runtime-started" and RUN != "da2093353a73"
assert PROJECT == "aletheia-k14-stg-" + RUN
assert (
    len(sys.argv) == 2
    and sys.argv[1].startswith("attempt-")
    and sys.argv[1][8:].isdigit()
)
OUT = ROOT / sys.argv[1]
OUT.mkdir()
CODE = OUT / "input"
CODE.mkdir()
for source in ROOT.glob("*.py"):
    (CODE / source.name).write_bytes(source.read_bytes())
EVENTS = []
SERIAL = 0


def docker(*args, timeout=35):
    return subprocess.check_output(["docker", *args], timeout=timeout).decode().strip()


def owner(kind, value):
    record = json.loads(docker(kind, "inspect", value))[0]
    labels = record["Config"]["Labels"] if kind == "container" else record["Labels"]
    assert labels["com.aletheia.k14.owner"] == RUN
    return record


def event(action, **detail):
    EVENTS.append({"event": action, "epoch": time.time(), **detail})
    with (OUT / "controller-events.jsonl").open("a", encoding="utf-8") as log:
        log.write(json.dumps(EVENTS[-1]) + "\n")


def write(name, value):
    write_json(OUT / name, value)


def wait(name, predicate=lambda _: True, timeout=50):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        file = OUT / name
        if file.exists():
            value = read_json(file)
            if predicate(value):
                return value
        time.sleep(0.04)
    raise TimeoutError(name)


def route(replicas, download=""):
    global SERIAL
    SERIAL += 1
    write(
        "edge-plan.json", {"serial": SERIAL, "replicas": replicas, "download": download}
    )
    event("planned_routes", serial=SERIAL, replicas=replicas, download=download)
    return SERIAL


def edge(serial, predicate=lambda _: True):
    return wait(
        "edge-state.json", lambda row: row["serial"] == serial and predicate(row)
    )


def mount(key, destination, readonly=True):
    volume = STATE["volumes"][key]
    owner("volume", volume)
    return [
        "--mount",
        f"type=volume,src={volume},dst={destination}"
        + (",readonly" if readonly else ""),
    ]


def container(label, alias, image, role, *command):
    header = [
        "run",
        "-d",
        "--pull=never",
        "--name",
        f"{PROJECT}-{OUT.name}-{label}",
        "--label",
        "com.aletheia.k14.owner=" + RUN,
        "--label",
        "com.aletheia.k18.attempt=" + OUT.name,
        "--network",
        STATE["network"],
        "--network-alias",
        alias,
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--entrypoint",
        "/opt/venv/bin/python",
    ]
    header += mount("configs", "/run/config")
    if role in {"api", "worker"}:
        header += [
            "--cpus",
            "0.75" if role == "api" else "1",
            "--memory",
            "512m" if role == "api" else "640m",
            "--pids-limit",
            "192",
            "--ulimit",
            "nofile=512:512",
            "--tmpfs",
            "/tmp:rw,size=134217728",
            "-e",
            "K14_COMPONENT=" + label,
            "--mount",
            f"type=bind,src={TOOLS},dst=/opt/k14,readonly",
        ]
        header += (
            mount(role + "-secrets", "/run/secrets")
            + mount("objects", "/var/lib/aletheia", False)
            + mount("metrics", "/run/metrics", False)
        )
        command = ("/opt/k14/stage_node.py", role)
    else:
        header += [
            "--cpus",
            "0.75",
            "--memory",
            "384m",
            "--pids-limit",
            "64",
            "--ulimit",
            "nofile=256:256",
            "--tmpfs",
            "/tmp:rw,size=67108864",
            "--mount",
            f"type=bind,src={CODE},dst=/code,readonly",
            "--mount",
            f"type=bind,src={OUT},dst=/evidence",
            "--mount",
            f"type=bind,src={TOOLS},dst=/opt/k14,readonly",
            "--env",
            "PYTHONPATH=/opt/k14",
        ]
        if role == "driver":
            header += mount("clients", "/run/load") + mount(
                "worker-secrets", "/run/worker"
            )
    value = docker(*header, image, "-B", *command)
    assert owner("container", value)["Image"] == image
    event("started", label=label, alias=alias, image=image, container=value)
    return value


def term(value):
    assert owner("container", value)["State"]["Running"]
    docker("kill", "--signal=TERM", value)
    event("sigterm", container=value)
    return EVENTS[-1]["epoch"]


def drained(value):
    code = int(docker("wait", value, timeout=45))
    assert code in (0, 241), code
    state = owner("container", value)["State"]
    assert not state["Running"] and not state["OOMKilled"]
    event(
        "drained_exit", container=value, exit_code=code, finished_at=state["FinishedAt"]
    )


def candidate(replicas, alias, label, image, download):
    serial = route([*replicas, alias], download)
    before = edge(
        serial,
        lambda step: (
            all(name in step["ready"] for name in replicas)
            and step["checks"].get(alias) is False
        ),
    )
    first = before["routed"].get(alias, 0)
    value = container(label, alias, image, "api")
    after = edge(
        serial,
        lambda step: alias in step["ready"] and step["routed"].get(alias, 0) > first,
    )
    assert after["serial"] == before["serial"]
    event(
        "candidate_ready_and_routed",
        alias=alias,
        image=image,
        before=before,
        after=after,
    )
    return value


def transition(direction, runtime, aliases, new_aliases, image):
    edge_candidate = candidate(
        aliases, new_aliases[0], direction + "-api0", image, aliases[0]
    )
    write(direction + "-arm.json", {"download_target": aliases[0]})
    cutover = wait(direction + "-cutover.json", timeout=35)
    event(
        "active_work_observed",
        direction=direction,
        **{k: v for k, v in cutover.items() if k != "epoch"},
    )
    worker_signal = term(runtime["worker0"])
    serial = route([aliases[1], new_aliases[0]])
    pool = edge(serial, lambda row: set(row["ready"]) == {aliases[1], new_aliases[0]})
    active_download = pool["active"].get(aliases[0], 0)
    assert active_download > 0, (direction, "download completed before drain")
    api_signal = term(runtime["api0"])
    write(
        direction + "-api-signal.json",
        {"epoch": api_signal, "active_at_drain": active_download},
    )
    drained(runtime["api0"])
    drained(runtime["worker0"])
    worker = container(direction + "-worker0", direction + "-worker0", image, "worker")
    second = candidate(
        [aliases[1], new_aliases[0]], new_aliases[1], direction + "-api1", image, ""
    )
    serial = route(new_aliases)
    edge(serial, lambda row: set(row["ready"]) == set(new_aliases))
    term(runtime["api1"])
    drained(runtime["api1"])
    term(runtime["worker1"])
    drained(runtime["worker1"])
    other_worker = container(
        direction + "-worker1", direction + "-worker1", image, "worker"
    )
    replaced = {
        "api0": edge_candidate,
        "api1": second,
        "worker0": worker,
        "worker1": other_worker,
    }
    assert all(
        owner("container", value)["Image"] == image for value in replaced.values()
    )
    write(
        direction + "-complete.json",
        {
            "worker_signal_epoch": worker_signal,
            "final_image": image,
            "replicas": replaced,
        },
    )
    wait(direction + "-result.json", timeout=50)
    return replaced


def main():
    assert owner("network", STATE["network"])["Internal"]
    previous, current = (IMAGES[x]["image_id"] for x in ("previous", "current"))
    pools = {
        role: STATE["containers"][role]
        for role in ("api0", "api1", "worker0", "worker1")
    }
    assert all(
        owner("container", value)["State"]["Running"]
        and owner("container", value)["Image"] == previous
        for value in pools.values()
    )
    serial = route(["api0", "api1"])
    gateway = container("edge", "k18-edge", current, "edge", "/code/edge.py")
    edge(serial, lambda row: set(row["ready"]) == {"api0", "api1"})
    driver = container("driver", "k18-driver", previous, "driver", "/code/traffic.py")
    try:
        wait("driver-ready.json", timeout=55)
        pools = transition(
            "upgrade", pools, ["api0", "api1"], ["api2", "api3"], current
        )
        pools = transition(
            "rollback", pools, ["api2", "api3"], ["api0", "api1"], previous
        )
        workers = [pools["worker0"], pools["worker1"]]
        docker("pause", *workers)
        try:
            write("fence-arm.json", {})
            wait("fence-claimed.json", timeout=15)
        finally:
            docker("unpause", *workers)
        write("fence-resume.json", {})
        wait("fence-result.json", timeout=45)
        assert docker("wait", driver, timeout=20) == "0"
        assert wait("traffic-result.json")["status"] == "passed"
        write(
            "controller-result.json",
            {
                "status": "passed",
                "images": IMAGES,
                "attempt": OUT.name,
                "resource_scope": "isolated two API and two worker fleet, up to three API during canary",
            },
        )
    finally:
        for name, value in (("traffic", driver), ("edge", gateway)):
            (OUT / (name + ".log")).write_text(docker("logs", value), encoding="utf-8")


if __name__ == "__main__":
    main()
