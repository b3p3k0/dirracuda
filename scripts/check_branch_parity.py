#!/usr/bin/env python3
"""Check branch promotion parity using patch-equivalent commit comparison.

Intended use: guard development -> main promotion PRs.

The script finds commits unique to each side using:
  git log --left-right --cherry-pick --no-merges BASE...HEAD

Then it classifies each unique commit as docs-only vs non-doc and optionally
requires explicit disposition lines for each non-doc unique commit in PR body.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List


DISPOSITION_KEYWORDS = ("ported", "superseded", "intentionally dropped")


@dataclass
class UniqueCommit:
    side: str  # "source_only" (HEAD only) or "target_only" (BASE only)
    sha: str
    subject: str
    files: List[str]
    docs_only: bool


def _run_git(args: List[str]) -> str:
    cmd = ["git", *args]
    try:
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        stdout = (exc.stdout or "").strip()
        detail = stderr or stdout or str(exc)
        raise RuntimeError(f"Git command failed: {' '.join(cmd)}\n{detail}") from exc
    return result.stdout


def _is_doc_file(path: str) -> bool:
    p = path.strip().lower()
    if not p:
        return True
    if p.startswith("docs/"):
        return True
    if p.startswith("gui/.github/issue_template/"):
        return True

    filename = os.path.basename(p)
    if filename in {
        "readme.md",
        "license",
        "license.md",
        "changelog.md",
        "contributing.md",
    }:
        return True

    ext = os.path.splitext(p)[1]
    return ext in {".md", ".rst", ".txt", ".adoc"}


def _commit_files(sha: str) -> List[str]:
    out = _run_git(["show", "--pretty=", "--name-only", "--no-renames", sha])
    return [line.strip() for line in out.splitlines() if line.strip()]


def _collect_unique_commits(base_ref: str, head_ref: str) -> List[UniqueCommit]:
    out = _run_git(
        [
            "log",
            "--left-right",
            "--cherry-pick",
            "--no-merges",
            "--pretty=format:%m%x09%H%x09%s",
            f"{base_ref}...{head_ref}",
        ]
    )

    commits: List[UniqueCommit] = []
    for line in out.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        marker, sha, subject = parts
        side = "target_only" if marker == "<" else "source_only" if marker == ">" else "unknown"
        files = _commit_files(sha)
        docs_only = bool(files) and all(_is_doc_file(path) for path in files)
        commits.append(
            UniqueCommit(
                side=side,
                sha=sha,
                subject=subject,
                files=files,
                docs_only=docs_only,
            )
        )
    return commits


def _load_pr_body(path: str | None) -> str:
    if not path:
        return ""
    try:
        return Path(path).read_text(encoding="utf-8")
    except Exception:
        return ""


def _has_required_checklist_markers(pr_body: str) -> tuple[bool, List[str]]:
    required_markers = [
        "Unique commits on source only",
        "Unique commits on target only",
    ]
    lower = pr_body.lower()
    missing = [m for m in required_markers if m.lower() not in lower]
    return (len(missing) == 0, missing)


def _missing_dispositions(pr_body: str, commits: Iterable[UniqueCommit]) -> List[UniqueCommit]:
    lines = pr_body.splitlines()
    unresolved: List[UniqueCommit] = []

    for commit in commits:
        full_sha = commit.sha.lower()
        short_sha = commit.sha[:7].lower()

        matched = False
        for line in lines:
            ll = line.lower()
            if full_sha in ll or short_sha in ll:
                if any(keyword in ll for keyword in DISPOSITION_KEYWORDS):
                    matched = True
                    break

        if not matched:
            unresolved.append(commit)

    return unresolved


def _print_summary(commits: List[UniqueCommit]) -> None:
    source_only = [c for c in commits if c.side == "source_only"]
    target_only = [c for c in commits if c.side == "target_only"]

    print("Branch parity summary")
    print("-" * 80)

    def _emit(title: str, rows: List[UniqueCommit]) -> None:
        print(title)
        if not rows:
            print("  (none)")
            return
        for c in rows:
            kind = "docs-only" if c.docs_only else "non-doc"
            print(f"  - {c.sha[:7]} [{kind}] {c.subject}")

    _emit("Unique commits on source (head) only:", source_only)
    _emit("Unique commits on target (base) only:", target_only)


def main() -> int:
    parser = argparse.ArgumentParser(description="Check promotion branch parity.")
    parser.add_argument("--base", required=True, help="Base ref/SHA (promotion target, e.g. main)")
    parser.add_argument("--head", required=True, help="Head ref/SHA (promotion source, e.g. development)")
    parser.add_argument("--pr-body", default=None, help="Path to PR body text file")
    parser.add_argument(
        "--require-dispositions",
        action="store_true",
        help="Require explicit dispositions in PR body for every non-doc unique commit",
    )
    parser.add_argument(
        "--enforce-checklist-markers",
        action="store_true",
        help="Require promotion checklist markers in PR body",
    )
    args = parser.parse_args()

    commits = _collect_unique_commits(args.base, args.head)
    _print_summary(commits)

    non_doc_unique = [c for c in commits if not c.docs_only and c.side in {"source_only", "target_only"}]

    pr_body = _load_pr_body(args.pr_body)
    failures: List[str] = []

    if args.enforce_checklist_markers:
        ok, missing = _has_required_checklist_markers(pr_body)
        if not ok:
            failures.append(
                "PR body is missing required promotion checklist markers: "
                + ", ".join(missing)
            )

    if args.require_dispositions and non_doc_unique:
        unresolved = _missing_dispositions(pr_body, non_doc_unique)
        if unresolved:
            bullets = "\n".join(
                f"  - {c.sha[:7]} ({c.side}) {c.subject}" for c in unresolved
            )
            failures.append(
                "Missing disposition entries for non-doc unique commits:\n" + bullets
            )

    if failures:
        print("\nParity check failed:\n")
        for failure in failures:
            print(f"- {failure}")
        print(
            "\nExpected disposition keywords per listed commit: "
            + ", ".join(DISPOSITION_KEYWORDS)
        )
        return 1

    print("\nParity check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
