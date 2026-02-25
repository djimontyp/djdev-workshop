#!/usr/bin/env python3
"""
digest-cli — CLI tool for querying Claude Code session transcripts.

Parses JSONL transcripts from ~/.claude/projects/, outputs structured JSON.
Designed to be called by agents, commands, and skills — not by hooks.

Subcommands:
  list      List sessions with metadata (filterable by date/project/min-turns)
  show      Show full session detail including dialog and files
  projects  List projects with session counts
  files     List modified files across sessions
  config    Show resolved configuration as JSON
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SESSIONS_BASE = Path.home() / ".claude" / "projects"

CACHE_DIR = Path.home() / ".cache" / "digest-cli"
CACHE_FILE = CACHE_DIR / "sessions.json"

VALID_CATEGORIES = {
    "feature", "bugfix", "refactor", "research", "config",
    "docs", "review", "debug", "testing", "deploy", "other",
}

DEFAULTS: dict[str, Any] = {
    "output_dir": "~/daily-summaries",
    "language": None,
    "min_turns": 3,
    "obsidian": {
        "enabled": False,
        "vault_path": "",
        "daily_notes_dir": "Daily notes",
        "date_format": "%Y-%m-%d",
        "folder_format": "%Y/%m",
        "section_heading": "## Notes",
        "wikilinks": True,
        "template_path": "",
    },
    "daily_format": {
        "group_by_project": True,
        "show_files": True,
        "show_branch": True,
        "show_worktree": True,
        "daily_summary": True,
        "summary_heading": "### Done",
        "min_duration": 0,
        "project_heading": "### \U0001f916 {project}",
    },
}

# ---------------------------------------------------------------------------
# Config System
# ---------------------------------------------------------------------------


def _deep_merge(base: dict, override: dict) -> dict:
    """Merge override into base recursively, returning a new dict."""
    result = base.copy()
    for key, value in override.items():
        if key.startswith("_"):
            continue
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _parse_frontmatter(text: str) -> dict[str, Any]:
    """Extract and parse YAML frontmatter from .local.md content."""
    result: dict[str, Any] = {}
    match = re.match(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
    if not match:
        return result
    for line in match.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, raw = line.partition(":")
        key = key.strip()
        raw = raw.strip()
        if not key:
            continue
        if raw == "" or raw.lower() in ("null", "~"):
            result[key] = None
        elif raw.lower() == "true":
            result[key] = True
        elif raw.lower() == "false":
            result[key] = False
        elif (raw.startswith('"') and raw.endswith('"')) or (
            raw.startswith("'") and raw.endswith("'")
        ):
            result[key] = raw[1:-1]
        elif re.fullmatch(r"-?\d+", raw):
            result[key] = int(raw)
        elif re.fullmatch(r"-?\d+\.\d+", raw):
            result[key] = float(raw)
        else:
            result[key] = raw
    return result


def _resolve_config_path(cwd: str) -> Path | None:
    """Resolve config file path via cascade (first found wins)."""
    env_path = os.environ.get("SESSION_DIGEST_CONFIG")
    if env_path:
        p = Path(env_path)
        if p.exists():
            return p
    candidates: list[Path] = []
    if cwd:
        candidates.append(Path(cwd) / ".claude" / "session-digest.local.md")
    candidates.append(Path.home() / ".claude" / "session-digest.local.md")
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _unflatten_config(flat: dict[str, Any]) -> dict[str, Any]:
    """Unflatten obsidian_* and daily_format keys into nested dicts."""
    user_config: dict[str, Any] = {}
    obsidian_overrides: dict[str, Any] = {}
    daily_format_overrides: dict[str, Any] = {}
    daily_format_keys = {
        "group_by_project", "show_files", "show_branch", "show_worktree",
        "daily_summary", "summary_heading", "min_duration", "project_heading",
    }
    for key, value in flat.items():
        if key.startswith("obsidian_"):
            obsidian_overrides[key[len("obsidian_"):]] = value
        elif key in daily_format_keys:
            daily_format_overrides[key] = value
        else:
            user_config[key] = value
    if obsidian_overrides:
        user_config["obsidian"] = obsidian_overrides
    if daily_format_overrides:
        user_config["daily_format"] = daily_format_overrides
    return user_config


def load_config(cwd: str = "") -> dict[str, Any]:
    """Load config via path cascade. Returns defaults if not found."""
    config_path = _resolve_config_path(cwd)
    if config_path is None:
        return _deep_merge(DEFAULTS, {})

    try:
        raw = config_path.read_text(encoding="utf-8-sig")
    except OSError:
        return _deep_merge(DEFAULTS, {})

    if config_path.suffix == ".json":
        try:
            user_config = json.loads(raw)
        except json.JSONDecodeError:
            return _deep_merge(DEFAULTS, {})
    else:
        user_flat = _parse_frontmatter(raw)
        if not user_flat:
            return _deep_merge(DEFAULTS, {})
        user_config = _unflatten_config(user_flat)

    config = _deep_merge(DEFAULTS, user_config)
    config["output_dir"] = str(Path(config["output_dir"]).expanduser())
    obsidian = config["obsidian"]
    if obsidian.get("vault_path"):
        obsidian["vault_path"] = str(Path(obsidian["vault_path"]).expanduser())
    if obsidian.get("template_path"):
        obsidian["template_path"] = str(Path(obsidian["template_path"]).expanduser())
    config["_config_path"] = str(config_path) if config_path else None
    return config


# ---------------------------------------------------------------------------
# JSONL Parsing
# ---------------------------------------------------------------------------


def _parse_user_text(content: Any) -> str:
    """Extract text from user message content."""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", "").strip())
        return " ".join(p for p in parts if p)
    return ""


def _parse_assistant_text(content: Any) -> str:
    """Extract text from assistant message content."""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", "").strip())
        return " ".join(p for p in parts if p)
    return ""


def count_turns_fast(transcript_path: Path) -> int:
    """Quick turn count without full transcript extraction."""
    if not transcript_path.exists():
        return 0
    count = 0
    try:
        with transcript_path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line or '"user"' not in line:
                    continue
                try:
                    entry = json.loads(line)
                    msg = entry.get("message", {})
                    if msg.get("role") == "user":
                        if _parse_user_text(msg.get("content", [])):
                            count += 1
                except (json.JSONDecodeError, AttributeError):
                    continue
    except OSError:
        pass
    return count


def extract_transcript(transcript_path: Path) -> dict[str, Any]:
    """Parse JSONL transcript and return structured data."""
    result: dict[str, Any] = {
        "user_messages": [],
        "files_modified": [],
        "dialog": "",
        "start_ts": None,
        "end_ts": None,
        "turn_count": 0,
        "git_branch": "",
    }
    if not transcript_path.exists():
        return result

    files_seen: set[str] = set()
    user_messages: list[str] = []
    dialog_parts: list[str] = []
    timestamps: list[float] = []
    git_branch = ""

    try:
        with transcript_path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                # Timestamps
                ts_raw = entry.get("timestamp")
                if ts_raw:
                    try:
                        ts = datetime.fromisoformat(
                            ts_raw.replace("Z", "+00:00")
                        ).timestamp()
                        timestamps.append(ts)
                    except (ValueError, TypeError):
                        pass

                # Git branch from cwd metadata
                if not git_branch:
                    branch = entry.get("gitBranch") or ""
                    if branch:
                        git_branch = branch

                msg = entry.get("message", {})
                role = msg.get("role", "")
                content = msg.get("content", [])

                if role == "user":
                    text = _parse_user_text(content)
                    if text:
                        result["turn_count"] += 1
                        user_messages.append(text)
                        dialog_parts.append(f"User: {text}")

                elif role == "assistant":
                    text = _parse_assistant_text(content)
                    if text:
                        if len(text) > 500:
                            text = text[:500] + "..."
                        dialog_parts.append(f"Assistant: {text}")

                    if isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "tool_use":
                                name = block.get("name", "")
                                if name in ("Write", "Edit", "NotebookEdit"):
                                    inputs = block.get("input", {}) or {}
                                    fpath = inputs.get("file_path") or inputs.get("notebook_path") or ""
                                    if fpath:
                                        files_seen.add(fpath)

                if entry.get("type") == "file-history-snapshot":
                    for f in entry.get("files", []):
                        fname = f.get("path") or f.get("filename") or ""
                        if fname:
                            files_seen.add(fname)
                    snapshot = entry.get("snapshot", {})
                    for fpath in snapshot.get("trackedFileBackups", {}):
                        if fpath:
                            files_seen.add(fpath)

    except OSError:
        pass

    result["user_messages"] = user_messages
    result["dialog"] = "\n".join(dialog_parts)
    result["git_branch"] = git_branch

    # Normalize file paths
    normalized: set[str] = set()
    for fpath in files_seen:
        name = Path(fpath).name
        existing = {p for p in normalized if Path(p).name == name}
        if existing:
            shortest = min(existing | {fpath}, key=len)
            normalized = (normalized - existing) | {shortest}
        else:
            normalized.add(fpath)
    result["files_modified"] = sorted(normalized)

    if timestamps:
        result["start_ts"] = min(timestamps)
        result["end_ts"] = max(timestamps)

    return result


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def format_duration(seconds: float) -> str:
    """Format seconds into human-readable duration."""
    minutes = int(seconds // 60)
    if minutes < 1:
        return "< 1m"
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    mins = minutes % 60
    if mins:
        return f"{hours}h {mins}m"
    return f"{hours}h"


def get_project_name(project_dir: str) -> str:
    """Extract human-readable project name from project path directory name."""
    # ~/.claude/projects/-Users-maks-project-name/UUID.jsonl
    # project dir name is like: -Users-maks-PycharmProjects-myproject
    name = project_dir.rsplit("-", 1)[-1] if "-" in project_dir else project_dir
    # Better: take the last path component from the decoded path
    decoded = project_dir.replace("-", "/")
    if decoded.startswith("/"):
        return Path(decoded).name
    return name


def get_project_path(project_dir: str) -> str:
    """Decode project directory name back to original path."""
    decoded = project_dir.replace("-", "/")
    if decoded.startswith("/"):
        return decoded
    return project_dir


# ---------------------------------------------------------------------------
# Session Scanning
# ---------------------------------------------------------------------------


def _scan_session_metadata(jsonl_path: Path) -> dict[str, Any] | None:
    """Lightweight scan: read first and last lines of JSONL for metadata."""
    try:
        size = jsonl_path.stat().st_size
        if size == 0:
            return None
    except OSError:
        return None

    first_ts = None
    last_ts = None
    git_branch = ""
    turn_count = 0

    try:
        with jsonl_path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                ts_raw = entry.get("timestamp")
                if ts_raw:
                    try:
                        ts = datetime.fromisoformat(
                            ts_raw.replace("Z", "+00:00")
                        )
                        if first_ts is None:
                            first_ts = ts
                        last_ts = ts
                    except (ValueError, TypeError):
                        pass

                if not git_branch:
                    branch = entry.get("gitBranch") or ""
                    if branch:
                        git_branch = branch

                msg = entry.get("message", {})
                if msg.get("role") == "user":
                    if _parse_user_text(msg.get("content", [])):
                        turn_count += 1

    except OSError:
        return None

    if first_ts is None:
        return None

    session_id = jsonl_path.stem
    project_dir = jsonl_path.parent.name
    project_name = get_project_name(project_dir)
    project_path = get_project_path(project_dir)

    duration_secs = (last_ts - first_ts).total_seconds() if last_ts and first_ts else 0

    return {
        "id": session_id,
        "project": project_name,
        "project_path": project_path,
        "date": first_ts.astimezone().strftime("%Y-%m-%d"),
        "start_time": first_ts.astimezone().strftime("%H:%M"),
        "end_time": last_ts.astimezone().strftime("%H:%M") if last_ts else "",
        "duration": format_duration(duration_secs),
        "duration_seconds": int(duration_secs),
        "turn_count": turn_count,
        "git_branch": git_branch,
        "_jsonl_path": str(jsonl_path),
        "_mtime": jsonl_path.stat().st_mtime,
        "_size": jsonl_path.stat().st_size,
    }


def _load_cache() -> dict[str, Any]:
    """Load metadata cache."""
    if not CACHE_FILE.exists():
        return {}
    try:
        data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _save_cache(cache: dict[str, Any]) -> None:
    """Save metadata cache."""
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps(cache), encoding="utf-8")
    except OSError:
        pass


def scan_all_sessions() -> list[dict[str, Any]]:
    """Scan all JSONL transcripts, using cache where valid."""
    if not SESSIONS_BASE.exists():
        return []

    cache = _load_cache()
    sessions: list[dict[str, Any]] = []
    new_cache: dict[str, Any] = {}

    for project_dir in SESSIONS_BASE.iterdir():
        if not project_dir.is_dir():
            continue
        for jsonl_path in project_dir.glob("*.jsonl"):
            key = str(jsonl_path)
            try:
                stat = jsonl_path.stat()
                mtime = stat.st_mtime
                size = stat.st_size
            except OSError:
                continue

            # Check cache validity
            cached = cache.get(key)
            if cached and cached.get("_mtime") == mtime and cached.get("_size") == size:
                sessions.append(cached)
                new_cache[key] = cached
                continue

            # Scan fresh
            meta = _scan_session_metadata(jsonl_path)
            if meta:
                sessions.append(meta)
                new_cache[key] = meta

    _save_cache(new_cache)

    # Sort by date + start_time descending
    sessions.sort(key=lambda s: (s["date"], s["start_time"]), reverse=True)
    return sessions


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------


def cmd_list(args: argparse.Namespace) -> None:
    """List sessions with optional filters."""
    sessions = scan_all_sessions()

    # Apply filters
    if args.date:
        if args.date == "today":
            target = datetime.now().strftime("%Y-%m-%d")
        elif args.date == "yesterday":
            target = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        else:
            target = args.date
        sessions = [s for s in sessions if s["date"] == target]

    if args.project:
        proj = args.project.lower()
        sessions = [s for s in sessions if proj in s["project"].lower()]

    if args.since:
        sessions = [s for s in sessions if s["date"] >= args.since]

    min_turns = args.min_turns if args.min_turns is not None else 0
    if min_turns > 0:
        sessions = [s for s in sessions if s["turn_count"] >= min_turns]

    # Clean internal fields
    output = []
    for s in sessions:
        clean = {k: v for k, v in s.items() if not k.startswith("_")}
        output.append(clean)

    json.dump({"sessions": output}, sys.stdout, indent=2, ensure_ascii=False)
    print()


def cmd_show(args: argparse.Namespace) -> None:
    """Show full session detail."""
    sessions = scan_all_sessions()

    # Find by ID or prefix
    target = args.session_id
    match = None
    for s in sessions:
        if s["id"] == target or s["id"].startswith(target):
            match = s
            break

    if not match:
        json.dump({"error": f"Session not found: {target}"}, sys.stdout, indent=2)
        print()
        sys.exit(1)

    jsonl_path = Path(match["_jsonl_path"])
    transcript = extract_transcript(jsonl_path)

    result = {k: v for k, v in match.items() if not k.startswith("_")}
    result["user_messages"] = transcript["user_messages"]
    result["files_modified"] = transcript["files_modified"]
    result["dialog"] = transcript["dialog"]

    json.dump(result, sys.stdout, indent=2, ensure_ascii=False)
    print()


def cmd_projects(args: argparse.Namespace) -> None:
    """List projects with session counts."""
    sessions = scan_all_sessions()

    if args.since:
        sessions = [s for s in sessions if s["date"] >= args.since]

    projects: dict[str, dict[str, Any]] = {}
    for s in sessions:
        name = s["project"]
        if name not in projects:
            projects[name] = {
                "project": name,
                "project_path": s["project_path"],
                "session_count": 0,
                "total_duration_seconds": 0,
                "last_session_date": s["date"],
            }
        projects[name]["session_count"] += 1
        projects[name]["total_duration_seconds"] += s["duration_seconds"]
        if s["date"] > projects[name]["last_session_date"]:
            projects[name]["last_session_date"] = s["date"]

    result = sorted(projects.values(), key=lambda p: p["session_count"], reverse=True)
    for p in result:
        p["total_duration"] = format_duration(p["total_duration_seconds"])

    json.dump({"projects": result}, sys.stdout, indent=2, ensure_ascii=False)
    print()


def cmd_files(args: argparse.Namespace) -> None:
    """List modified files across sessions."""
    sessions = scan_all_sessions()

    if args.date:
        if args.date == "today":
            target = datetime.now().strftime("%Y-%m-%d")
        elif args.date == "yesterday":
            target = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        else:
            target = args.date
        sessions = [s for s in sessions if s["date"] == target]

    if args.project:
        proj = args.project.lower()
        sessions = [s for s in sessions if proj in s["project"].lower()]

    all_files: dict[str, list[str]] = {}
    for s in sessions:
        jsonl_path = Path(s["_jsonl_path"])
        transcript = extract_transcript(jsonl_path)
        for f in transcript["files_modified"]:
            if f not in all_files:
                all_files[f] = []
            all_files[f].append(s["id"])

    result = [
        {"file": f, "session_count": len(sids), "sessions": sids}
        for f, sids in sorted(all_files.items())
    ]

    json.dump({"files": result}, sys.stdout, indent=2, ensure_ascii=False)
    print()


def cmd_config(args: argparse.Namespace) -> None:
    """Show resolved configuration."""
    config = load_config(os.getcwd())
    config_path = config.pop("_config_path", None)

    result: dict[str, Any] = {"config_path": config_path}
    result.update(config)

    json.dump(result, sys.stdout, indent=2, ensure_ascii=False)
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="digest-cli",
        description="CLI tool for querying Claude Code session transcripts",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # list
    p_list = subparsers.add_parser("list", help="List sessions")
    p_list.add_argument("--date", help="Filter by date (today, yesterday, YYYY-MM-DD)")
    p_list.add_argument("--project", help="Filter by project name (substring match)")
    p_list.add_argument("--since", help="Show sessions since date (YYYY-MM-DD)")
    p_list.add_argument("--min-turns", type=int, help="Minimum turn count")
    p_list.set_defaults(func=cmd_list)

    # show
    p_show = subparsers.add_parser("show", help="Show session detail")
    p_show.add_argument("session_id", help="Session ID or prefix")
    p_show.set_defaults(func=cmd_show)

    # projects
    p_projects = subparsers.add_parser("projects", help="List projects")
    p_projects.add_argument("--since", help="Filter since date (YYYY-MM-DD)")
    p_projects.set_defaults(func=cmd_projects)

    # files
    p_files = subparsers.add_parser("files", help="List modified files")
    p_files.add_argument("--date", help="Filter by date (today, yesterday, YYYY-MM-DD)")
    p_files.add_argument("--project", help="Filter by project name")
    p_files.set_defaults(func=cmd_files)

    # config
    p_config = subparsers.add_parser("config", help="Show resolved config")
    p_config.set_defaults(func=cmd_config)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
