"""Resolve installed production requirements and emit path-free pins, licenses, SPDX."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
import tomllib
from datetime import UTC, datetime
from importlib.metadata import distribution
from pathlib import Path
from uuid import uuid4

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

_LICENSES = {
    "MIT",
    "MIT-0",
    "MIT-CMU",
    "CNRI-Python",
    "Apache-2.0",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "ISC",
    "MPL-2.0",
    "PSF-2.0",
    "Python-2.0",
    "CC0-1.0",
    "0BSD",
    "Zlib",
    "Unlicense",
    "LGPL-2.1-only",
    "LGPL-2.1-or-later",
    "LGPL-3.0-only",
    "LGPL-3.0-or-later",
}


def _license(metadata) -> str:
    value = metadata.get("License-Expression") or metadata.get("License") or ""
    if value in _LICENSES:
        return value
    classifiers = metadata.get_all("Classifier", [])
    if "License :: OSI Approved :: MIT License" in classifiers or value.startswith("MIT License\n"):
        return "MIT"
    if "License :: OSI Approved :: ISC License (ISCL)" in classifiers or value == "ISC License":
        return "ISC"
    # Keep valid simple dual declarations; all other forms remain explicit unknown.
    terms = value.split()
    if (
        terms
        and len(value) < 256
        and len(terms) % 2 == 1
        and all(
            term in (_LICENSES if index % 2 == 0 else {"AND", "OR"})
            for index, term in enumerate(terms)
        )
    ):
        return value
    return "NOASSERTION"


def _notice_file(name: str) -> bool:
    value = name.lower()
    return value.startswith(
        ("license", "copying", "notice", "authors", "copyright")
    ) or value.endswith(("-license.txt", "-copying.txt", "-copying-gpl.txt"))


def _review(
    name: str, version: str, documents: list[dict], texts: dict[str, str], *, complete: bool = True
) -> tuple[dict | None, dict | None]:
    """Only previously reviewed exact notice sets gain an identifier; drift fails closed."""
    ledger = json.loads(Path(__file__).with_name("k16_license_reviews.json").read_text())
    candidate = next((r for r in ledger["reviews"] if r["name"] == name), None)
    if candidate is None:
        return None, None
    exact = (
        complete
        and candidate["version"] == version
        and candidate["license_files_sha256"] == {d["file"]: d["sha256"] for d in documents}
        and candidate.get("platform") in (None, sys.platform)
        and candidate.get("machine") in (None, platform.machine())
    )
    details = {
        "status": "exact_notice_set_identified" if exact else "reviewed_notice_set_changed",
        "basis": "Installed license documents, exact package version and full discovered notice-file SHA-256 set.",
        "primary_sources": candidate["primary_sources"],
        "comment": candidate["comment"],
        "scope": ledger["scope"],
    }
    if not exact:
        return {"expression": "NOASSERTION", **details}, None
    reference = None
    if candidate["license_ref"]:
        reference = {
            "licenseId": candidate["license_ref"],
            "name": name + " " + version + " exact installed distribution notices",
            "extractedText": "\n\n".join(
                "FILE: " + key + "\n" + texts[key] for key in sorted(texts)
            ),
            "seeAlsos": candidate["primary_sources"],
            "comment": candidate["comment"] + " " + ledger["scope"],
        }
    return {"expression": candidate["license_expression"], **details}, reference


def build(root: Path, extras: tuple[str, ...] = ("hosted",)) -> tuple[dict, dict]:
    raw = (root / "pyproject.toml").read_bytes()
    project = tomllib.loads(raw.decode())["project"]
    declared = list(project.get("dependencies", []))
    for extra in extras:
        declared.extend(project["optional-dependencies"][extra])
    environment = default_environment()
    queue = []
    for text in declared:
        requirement = Requirement(text)
        if requirement.marker is None or any(
            requirement.marker.evaluate(environment | {"extra": extra}) for extra in ("", *extras)
        ):
            queue.append((requirement, "aletheia-lab"))
    packages, selected_extras, edges, extracted_licenses = {}, {}, set(), {}
    while queue:
        requirement, parent = queue.pop()
        if requirement.url is not None:
            raise ValueError("Direct URL requirements require separate credential-free provenance.")
        name = canonicalize_name(requirement.name)
        dist = distribution(name)
        version = dist.version
        if not requirement.specifier.contains(Version(version), prereleases=True):
            raise ValueError("Installed dependency violates declared version constraints: " + name)
        edges.add((parent, name))
        requested = selected_extras.get(name, set()) | set(requirement.extras)
        if name in packages and requested == selected_extras[name]:
            continue
        selected_extras[name] = requested
        license_evidence, license_texts, complete = [], {}, True
        site = Path(dist.locate_file("")).resolve()
        for entry in sorted(dist.files or [], key=str):
            if len(license_evidence) >= 64:
                complete = False
                break
            if not _notice_file(entry.name):
                continue
            file = Path(dist.locate_file(entry)).resolve()
            if file.is_relative_to(site) and file.is_file() and file.stat().st_size <= 2 * 1024**2:
                data = file.read_bytes()
                license_evidence.append(
                    {
                        "file": entry.as_posix(),
                        "sha256": hashlib.sha256(data).hexdigest(),
                        "bytes": len(data),
                    }
                )
                license_texts[entry.as_posix()] = data.decode("utf-8")
            else:
                complete = False
        review, extracted = _review(
            name, version, license_evidence, license_texts, complete=complete
        )
        if extracted is not None:
            extracted_licenses[extracted["licenseId"]] = extracted
        packages[name] = {
            "name": name,
            "version": version,
            "license_declared": review["expression"]
            if review is not None
            else _license(dist.metadata),
            "license_identification": review if review is not None else {"status": "metadata_only"},
            "license_documents": license_evidence,
            "license_classifiers": sorted(
                x for x in dist.metadata.get_all("Classifier", []) if x.startswith("License ::")
            ),
            "metadata_sha256": hashlib.sha256(
                (dist.read_text("METADATA") or "").encode()
            ).hexdigest(),
        }
        for text in dist.requires or []:
            child = Requirement(text)
            if child.marker is None or any(
                child.marker.evaluate(environment | {"extra": extra}) for extra in ("", *requested)
            ):
                queue.append((child, name))
    inventory = {
        "schema_version": "hosted-resolved-inventory/v1",
        "project": project["name"],
        "project_version": project["version"],
        "selected_extras": list(extras),
        "python_version": platform.python_version(),
        "platform": sys.platform,
        "machine": platform.machine(),
        "pyproject_sha256": hashlib.sha256(raw).hexdigest(),
        "packages": [packages[n] for n in sorted(packages)],
        "dependency_edges": [list(edge) for edge in sorted(edges)],
        "scope": "Installed environment dependency closure; exact versions, not wheel artifact hashes or another OS/image qualification.",
    }
    inventory["inventory_sha256"] = hashlib.sha256(
        json.dumps(inventory, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    root_id = "SPDXRef-Package-aletheia-lab"
    spdx_packages = [
        {
            "SPDXID": root_id,
            "name": project["name"],
            "versionInfo": project["version"],
            "downloadLocation": "NOASSERTION",
            "filesAnalyzed": False,
            "licenseConcluded": "NOASSERTION",
            "licenseDeclared": "NOASSERTION",
            "copyrightText": "NOASSERTION",
        }
    ]
    for item in inventory["packages"]:
        spdx_packages.append(
            {
                "SPDXID": "SPDXRef-Package-" + item["name"],
                "name": item["name"],
                "versionInfo": item["version"],
                "downloadLocation": "NOASSERTION",
                "filesAnalyzed": False,
                "licenseConcluded": "NOASSERTION",
                "licenseDeclared": item["license_declared"],
                "copyrightText": "NOASSERTION",
                "externalRefs": [
                    {
                        "referenceCategory": "PACKAGE-MANAGER",
                        "referenceType": "purl",
                        "referenceLocator": "pkg:pypi/" + item["name"] + "@" + item["version"],
                    }
                ],
                "comment": "Installed metadata and hash-gated reviewed license notices. Custom LicenseRef preserves exact multiple notices; no compatibility approval. Binary files not analyzed.",
            }
        )
    spdx = {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": "aletheia-hosted-installed-dependencies",
        "documentNamespace": "urn:uuid:" + str(uuid4()),
        "creationInfo": {
            "created": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "creators": ["Tool: aletheia-dependency-report-1"],
        },
        "documentDescribes": [root_id],
        "packages": spdx_packages,
        "relationships": [
            {
                "spdxElementId": "SPDXRef-Package-" + parent,
                "relationshipType": "DEPENDS_ON",
                "relatedSpdxElement": "SPDXRef-Package-" + child,
            }
            for parent, child in sorted(edges)
        ],
        "comment": "Python installed dependency metadata only. OS/base-image packages and binary identity need image SBOM/build evidence.",
    }
    if extracted_licenses:
        spdx["hasExtractedLicensingInfos"] = [
            extracted_licenses[k] for k in sorted(extracted_licenses)
        ]
    return inventory, spdx


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--extras", default="hosted")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        inventory, spdx = build(args.root, tuple(x for x in args.extras.split(",") if x))
        args.output.mkdir(parents=True, exist_ok=False)
        for name, value in [("inventory.json", inventory), ("sbom.spdx.json", spdx)]:
            (args.output / name).write_text(
                json.dumps(value, sort_keys=True, ensure_ascii=True, indent=2) + "\n"
            )
        (args.output / "requirements-observed.txt").write_text(
            "# Exact versions observed in inventory.json platform; artifact hashes and otherOS require own proof.\n"
            + "".join(
                item["name"] + "==" + item["version"] + "\n" for item in inventory["packages"]
            )
        )
        print(
            json.dumps(
                {
                    "packages": len(inventory["packages"]),
                    "license_unknown": sum(
                        item["license_declared"] == "NOASSERTION" for item in inventory["packages"]
                    ),
                    "inventory_sha256": inventory["inventory_sha256"],
                    "scope": inventory["scope"],
                }
            )
        )
        return 0
    except Exception:
        print('{"error":"dependency_report_unavailable"}', file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
