from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any


def collect_workspace_diff(project_root: str) -> dict[str, Any]:
    root = Path(str(project_root)).resolve()
    if not root.exists():
        return {"summary": {"files": 0, "additions": 0, "deletions": 0}, "files": []}
    if not _is_git_repo(root):
        return {"summary": {"files": 0, "additions": 0, "deletions": 0}, "files": []}

    files: dict[str, dict[str, Any]] = {}
    for command in (
        ["git", "-C", str(root), "diff", "--numstat", "--no-ext-diff"],
        ["git", "-C", str(root), "diff", "--cached", "--numstat", "--no-ext-diff"],
    ):
        completed = _run(command)
        if completed.returncode != 0:
            continue
        for raw_line in completed.stdout.splitlines():
            parts = raw_line.split("\t", 2)
            if len(parts) != 3:
                continue
            additions = _to_int(parts[0])
            deletions = _to_int(parts[1])
            path = parts[2].strip()
            if not path:
                continue
            item = files.setdefault(path, {"file": path, "additions": 0, "deletions": 0, "before": "", "after": ""})
            item["additions"] += additions
            item["deletions"] += deletions

    status = _run(["git", "-C", str(root), "status", "--porcelain=v1", "--untracked-files=all"])
    if status.returncode == 0:
        for raw_line in status.stdout.splitlines():
            line = raw_line.rstrip()
            if not line:
                continue
            path = _status_path(line)
            if not path:
                continue
            item = files.setdefault(path, {"file": path, "additions": 0, "deletions": 0, "before": "", "after": ""})
            if line.startswith("??"):
                full_path = root / path
                if full_path.is_file():
                    try:
                        item["additions"] = max(item["additions"], len(full_path.read_text(encoding="utf-8", errors="replace").splitlines()))
                    except OSError:
                        item["additions"] = max(item["additions"], 1)
                else:
                    item["additions"] = max(item["additions"], 1)

    ordered = sorted(files.values(), key=lambda item: str(item.get("file") or ""))
    summary = {
        "files": len(ordered),
        "additions": sum(int(item.get("additions", 0)) for item in ordered),
        "deletions": sum(int(item.get("deletions", 0)) for item in ordered),
    }
    return {"summary": summary, "files": ordered}


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return subprocess.CompletedProcess(command, returncode=1, stdout="", stderr="")


def _is_git_repo(root: Path) -> bool:
    completed = _run(["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"])
    return completed.returncode == 0 and completed.stdout.strip().lower() == "true"


def _status_path(line: str) -> str | None:
    if len(line) < 4:
        return None
    path = line[3:].strip()
    if " -> " in path:
        _, _, path = path.partition(" -> ")
    return path or None


def _to_int(value: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
