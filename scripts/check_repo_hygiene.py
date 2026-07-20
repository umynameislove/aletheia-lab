#!/usr/bin/env python3
"""Repository-hygiene guard for Aletheia Lab.

The default scan checks Git-tracked files, which is the exact scope that can be
published. ``--all`` scans the working directory while excluding dependency,
VCS, and cache directories.

The guard rejects:

* internal planning or administrative tracking artifacts;
* office documents and other inappropriate binary project records;
* generated caches and editor junk;
* self-grading, unverifiable promotional language, and AI attribution metadata.

Warnings do not block the default scan. ``--strict`` promotes warnings to
errors, which is useful during release preparation.

Usage:
    python scripts/check_repo_hygiene.py
    python scripts/check_repo_hygiene.py --root . --strict
    python scripts/check_repo_hygiene.py --all
"""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

JUNK_DIR_NAMES = {
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".ipynb_checkpoints",
}
JUNK_FILE_NAMES = {".DS_Store", "Thumbs.db"}
JUNK_SUFFIXES = {".pyc", ".pyo"}
FORBIDDEN_SUFFIXES = {".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls"}
FORBIDDEN_DIR_NAMES = {"tracking"}

TRACKING_NAME_PATTERNS = [
    r"master[_-]?plan",
    r"phase[_-]?tracking",
    r"phase[_-]?roadmap",
    r"roadmap",
    r"micro[_-]?task",
    r"scorecard",
    r"risk[_-]?register",
    r"decision[_-]?log",
    r"experiment[_-]?log",
    r"paper[_-]?plan",
    r"defense[_-]?notes",
    r"project[_-]?brief",
    r"implementation[_-]?order",
    r"reuse[_-]?map",
    r"related[_-]?work[_-]?(alignment|amendment)",
    r"do[_-]?an[_-]?tot[_-]?nghiep",
]
TRACKING_RE = re.compile("|".join(TRACKING_NAME_PATTERNS), re.IGNORECASE)

CONTENT_RED_FLAGS = [
    re.compile(r"danh\s*v[oọ]ng", re.IGNORECASE),
    re.compile(r"score\s*k[yỳ]\s*v[oọ]ng", re.IGNORECASE),
    re.compile(r"paper\s*potential", re.IGNORECASE),
    re.compile(r"defense\s*readiness", re.IGNORECASE),
    re.compile(r"\b\d+(?:\.\d+)?(?:\s*-\s*\d+(?:\.\d+)?)?\s*/\s*10\b"),  # self-grade N/10
    re.compile(r"generated\s+by\s+(ai|claude|gpt)\b", re.IGNORECASE),
    re.compile(r"co-authored-by:\s*(claude|assistant|gpt)", re.IGNORECASE),
    re.compile(r"\bplanned_after[_a-z0-9-]*\s*:", re.IGNORECASE),
    re.compile(r"\b(deadline_policy|official_case_goal|next_step)\s*:", re.IGNORECASE),
    re.compile(r"^##\s*(task id|phase\s*/\s*module)\s*$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"implementation-facing\s+v\d", re.IGNORECASE),
]

WALK_EXCLUDED_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "data",
    "experiments",
    "reports",
}
ALLOW_RELATIVE = {
    "scripts/check_repo_hygiene.py",
    "src/aletheia_lab/reporting/tables.py",
}


def git_tracked(root: Path) -> list[Path] | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "ls-files"], capture_output=True, text=True, check=True
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    files = [root / line for line in out.stdout.splitlines() if line.strip()]
    return files or None


def fs_paths(root: Path):
    for p in root.rglob("*"):
        parts = p.relative_to(root).parts
        if set(parts) & WALK_EXCLUDED_DIRS:
            continue
        # Report the cache directory once without traversing every generated file.
        if set(parts[:-1]) & JUNK_DIR_NAMES:
            continue
        yield p


def scan_content(p: Path) -> str | None:
    if p.suffix.lower() not in {".md", ".yaml", ".yml", ".py"} or not p.is_file():
        return None
    try:
        text = p.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    for rx in CONTENT_RED_FLAGS:
        if rx.search(text):
            return rx.pattern
    return None


def check(root: Path, use_git: bool):
    errors: list[str] = []
    warns: list[str] = []
    tracked = git_tracked(root) if use_git else None
    paths = tracked if tracked is not None else list(fs_paths(root))

    for p in paths:
        try:
            rel = p.relative_to(root).as_posix()
        except ValueError:
            continue
        name = p.name
        parent_parts = set(p.relative_to(root).parts[:-1])

        junk_parent = sorted(parent_parts & JUNK_DIR_NAMES)
        if junk_parent:
            warns.append(f"[JUNK DIR]      {junk_parent[0]}/")
            continue
        forbidden_parent = sorted(parent_parts & FORBIDDEN_DIR_NAMES)
        if forbidden_parent:
            errors.append(
                f"[TRACKING DIR]  {forbidden_parent[0]}/  -> "
                "internal tracking must remain outside the repository"
            )
            continue

        if p.is_dir():
            if name in JUNK_DIR_NAMES:
                warns.append(f"[JUNK DIR]      {rel}/")
            elif name in FORBIDDEN_DIR_NAMES:
                errors.append(
                    f"[TRACKING DIR]  {rel}/  -> internal tracking must remain outside the repository"
                )
            elif not any(p.iterdir()):
                warns.append(f"[EMPTY DIR]     {rel}/  -> add .gitkeep or remove the directory")
            continue

        if name in JUNK_FILE_NAMES or p.suffix in JUNK_SUFFIXES:
            warns.append(f"[JUNK FILE]     {rel}")
            continue
        if p.suffix.lower() in FORBIDDEN_SUFFIXES:
            errors.append(
                f"[OFFICE FILE]   {rel}  -> office documents do not belong in the code repository"
            )
            continue
        if rel in ALLOW_RELATIVE:
            continue
        if "adr/" not in rel and TRACKING_RE.search(name):
            errors.append(
                f"[TRACKING FILE] {rel}  -> move internal tracking outside the repository"
            )
            continue
        flag = scan_content(p)
        if flag:
            errors.append(f"[SELF-GRADE/AI] {rel}  -> matched '{flag}'")

    return sorted(set(errors)), sorted(set(warns)), (tracked is not None)


def main() -> int:
    ap = argparse.ArgumentParser(description="Aletheia Lab repo hygiene guard")
    ap.add_argument("--root", default=".")
    ap.add_argument(
        "--all", action="store_true", help="scan the working directory instead of Git-tracked files"
    )
    ap.add_argument("--strict", action="store_true", help="treat warnings as errors")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    errors, warns, via_git = check(root, use_git=not args.all)
    src = "git-tracked files" if via_git else "filesystem"
    print(f"Hygiene scan ({src}) in {root.name}/")

    for w in warns:
        print("  WARN  " + w)
    for e in errors:
        print("  ERROR " + e)

    blocking = errors + (warns if args.strict else [])
    if not blocking:
        msg = "PASS"
        if warns:
            msg += f" ({len(warns)} non-blocking ignored-file warnings)"
        print(f"\nOK: repo hygiene {msg}.")
        return 0
    print(
        f"\nFAIL: {len(errors)} error"
        + (f", {len(warns)} warning (strict)" if args.strict else "")
        + ". Keep source code in the repository and internal planning records outside it."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
