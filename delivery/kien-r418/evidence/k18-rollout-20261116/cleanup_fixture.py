"""Archive runtime logs and remove only the labelled K18.03 fixture."""

import json
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATE = json.loads((ROOT / "state-public.json").read_text(encoding="utf-8"))
RUN = "9fc7adf58fe1"
assert STATE["run_id"] == RUN and STATE["project"] == "aletheia-k14-stg-" + RUN


def docker(*args):
    return subprocess.check_output(["docker", *args], timeout=35).decode()


records = []
ids = docker("ps", "-aq", "--filter", "label=com.aletheia.k14.owner=" + RUN).split()
assert len(ids) >= 7
with zipfile.ZipFile(
    ROOT / "runtime-logs.zip", "x", compression=zipfile.ZIP_DEFLATED
) as archive:
    for value in ids:
        info = json.loads(docker("inspect", value))[0]
        assert info["Config"]["Labels"]["com.aletheia.k14.owner"] == RUN
        records.append(
            {
                "name": info["Name"],
                "image": info["Image"],
                "state": info["State"],
                "limits": {
                    key: info["HostConfig"][key]
                    for key in ("Memory", "NanoCpus", "PidsLimit", "Ulimits")
                },
            }
        )
        log = subprocess.run(
            ["docker", "logs", value], capture_output=True, check=True, timeout=20
        )
        archive.writestr(info["Name"].lstrip("/") + ".log", log.stdout + log.stderr)
assert not any(row["state"]["OOMKilled"] for row in records)
(ROOT / "final-runtime-inspection.json").write_text(
    json.dumps(records, indent=2), encoding="utf-8"
)
for volume in STATE["volumes"].values():
    assert (
        json.loads(docker("volume", "inspect", volume))[0]["Labels"][
            "com.aletheia.k14.owner"
        ]
        == RUN
    )
assert (
    json.loads(docker("network", "inspect", STATE["network"]))[0]["Labels"][
        "com.aletheia.k14.owner"
    ]
    == RUN
)
docker("container", "rm", "-f", *ids)
docker("volume", "rm", *STATE["volumes"].values())
docker("network", "rm", STATE["network"])
assert not docker(
    "ps", "-aq", "--filter", "label=com.aletheia.k14.owner=" + RUN
).strip()
remaining_lan = docker(
    "ps", "-q", "--filter", "label=com.aletheia.k14.owner=da2093353a73"
).split()
result = {
    "owner": RUN,
    "removed_containers": len(ids),
    "removed_volumes": len(STATE["volumes"]),
    "removed_network": True,
    "no_oom": True,
    "lan_staging_running_containers": len(remaining_lan),
}
(ROOT / "cleanup.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
print(json.dumps(result))
