"""Build the preceding commit offline and provision an isolated rollout fixture."""

import hashlib
import importlib.util
import io
import json
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path
from uuid import uuid4

OUT = Path(__file__).resolve().parent
REPO = Path.home() / ".codex/worktrees/product-api-publication/_repo_aletheia_ref"
TOOLS = REPO / "delivery/kien-r418/staging/k14-local"
CURRENT = "fa46dd8cbb2239c6f116456894a2009b45846245"


def git(*args):
    return subprocess.check_output(["git", *args], cwd=REPO, timeout=45)


def docker(*args):
    return subprocess.check_output(["docker", *args], timeout=30).decode().strip()


def wait_for(command, service):
    for _ in range(70):
        attempt = subprocess.run(command, capture_output=True, check=False, timeout=10)
        if attempt.returncode == 0:
            return
        time.sleep(2)
    raise RuntimeError(f"{service} did not become ready")


def main():
    reference = json.loads(
        (REPO / "delivery/kien-r418/evidence/k14-staging/state-public.json").read_text(
            encoding="utf-8"
        )
    )
    assert reference["head"] == CURRENT
    assert (
        docker("image", "inspect", "--format", "{{.Id}}", reference["image"])
        == reference["image_id"]
    )
    previous = git("rev-parse", CURRENT + "^").decode().strip()
    source = git(
        "archive",
        previous,
        "src",
        "pyproject.toml",
        "README.md",
        "LICENSE",
        "MANIFEST.in",
    )
    source_hash = hashlib.sha256(source).hexdigest()
    run = uuid4().hex[:12]
    tag = "aletheia-k18-rollout:" + previous[:8] + "-" + run
    private = Path(tempfile.mkdtemp(prefix="k1803-" + run + "-", dir="C:/t"))

    context = private / "build-context"
    context.mkdir()
    with tarfile.open(fileobj=io.BytesIO(source)) as tree:
        tree.extractall(context, filter="data")
    (context / "Dockerfile").write_bytes((TOOLS / "Dockerfile").read_bytes())
    with (OUT / "build-previous-02.log").open("xb") as log:
        subprocess.run(
            [
                "docker",
                "build",
                "--network",
                "none",
                "--pull=false",
                "--tag",
                tag,
                "--build-arg",
                "SOURCE_DATE_EPOCH="
                + git("show", "-s", "--format=%ct", previous).decode().strip(),
                "--build-arg",
                "GIT_HEAD=" + previous,
                "--build-arg",
                "ALETHEIA_RELEASE_ID=" + source_hash,
                str(context),
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
            check=True,
            timeout=240,
        )
    older = docker("image", "inspect", "--format", "{{.Id}}", tag)
    assert older != reference["image_id"]
    images = {
        "previous": {
            "revision": previous,
            "image_id": older,
            "tag": tag,
            "release_identity": source_hash,
        },
        "current": {
            "revision": CURRENT,
            "image_id": reference["image_id"],
            "tag": reference["image"],
            "release_identity": reference["release_identity"],
        },
    }
    (OUT / "images.json").write_text(json.dumps(images, indent=2), encoding="utf-8")
    state = {
        "head": previous,
        "image": tag,
        "image_id": older,
        "release_identity": source_hash,
        "run_id": run,
        "project": "aletheia-k14-stg-" + run,
        "phase": "image-built",
        "private_dir": str(private),
    }
    with (OUT / "state-public.json").open("x", encoding="utf-8") as stream:
        json.dump(state, stream, indent=2)

    spec = importlib.util.spec_from_file_location("stage_up", TOOLS / "stage_up.py")
    stage = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(stage)
    stage.OUT = OUT
    stage.ROOT = REPO
    stage.main("dependencies")
    state = json.loads((OUT / "state-public.json").read_text(encoding="utf-8"))
    assert state["run_id"] == run
    wait_for(
        [
            "docker",
            "exec",
            state["containers"]["pg"],
            "pg_isready",
            "-h",
            "127.0.0.1",
            "-d",
            "aletheia",
        ],
        "PostgreSQL",
    )
    wait_for(
        [
            "docker",
            "exec",
            state["containers"]["idp"],
            "bash",
            "-c",
            "exec 3<>/dev/tcp/127.0.0.1/$1",
            "ready",
            str(state["configuration"]["idp_port"]),
        ],
        "Keycloak TLS",
    )
    stage.main("start-runtime")


if __name__ == "__main__":
    main()
