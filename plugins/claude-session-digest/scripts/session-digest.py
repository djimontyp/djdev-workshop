#!/usr/bin/env python3
"""
claude-session-digest — SessionEnd hook script

Reads SessionEnd JSON from stdin, extracts transcript data, generates an AI summary
(optional), and writes a structured entry to a daily markdown digest.

Config cascade (first found wins):
  1. SESSION_DIGEST_CONFIG env var       — explicit override
  2. {cwd}/.claude/session-digest.local.md  — per-project override
  3. ~/.claude/session-digest.local.md      — user-level defaults

Docs:   https://github.com/djimontyp/djdev-workshop/tree/main/plugins/claude-session-digest
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULTS: dict[str, Any] = {
    "output_dir": "~/daily-summaries",
    "language": None,
    "model": "haiku",
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
        "show_tools": True,
        "show_files": True,
        "show_branch": True,
        "project_heading": "### \U0001f916 {project}",
        "entry_format": "**{time}** \u00b7 `{category}` \u00b7 {duration}",
    },
}

# ---------------------------------------------------------------------------
# Config System (Phase 1)
# ---------------------------------------------------------------------------


def _deep_merge(base: dict, override: dict) -> dict:
    """Merge override into base recursively, returning a new dict."""
    result = base.copy()
    for key, value in override.items():
        if key.startswith("_"):
            continue  # skip _doc/_comment fields from example config
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _parse_frontmatter(text: str) -> dict[str, Any]:
    """Extract and parse YAML frontmatter from .local.md content.

    Handles flat key: value lines between --- markers.
    Supported types: strings, integers, floats, booleans (true/false), null.
    Ignores # comments and empty lines.
    """
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
    """Resolve config file path via cascade (first found wins).

    Priority:
    1. SESSION_DIGEST_CONFIG env var        — explicit override (backward compat)
    2. {cwd}/.claude/session-digest.local.md — per-project override
    3. ~/.claude/session-digest.local.md     — user-level defaults
    """
    env_path = os.environ.get("SESSION_DIGEST_CONFIG")
    if env_path:
        p = Path(env_path)
        if p.exists():
            return p
        print(
            f"[session-digest] SESSION_DIGEST_CONFIG={env_path} not found, falling back to cascade",
            file=sys.stderr,
        )

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
        "group_by_project",
        "show_tools",
        "show_files",
        "show_branch",
        "project_heading",
        "entry_format",
    }

    for key, value in flat.items():
        if key.startswith("obsidian_"):
            obsidian_overrides[key[len("obsidian_") :]] = value
        elif key in daily_format_keys:
            daily_format_overrides[key] = value
        else:
            user_config[key] = value

    if obsidian_overrides:
        user_config["obsidian"] = obsidian_overrides
    if daily_format_overrides:
        user_config["daily_format"] = daily_format_overrides

    return user_config


def load_config(cwd: str = "") -> dict[str, Any] | None:
    """Load config via path cascade. Returns None if not found (prints hint)."""
    config_path = _resolve_config_path(cwd)

    if config_path is None:
        user_path = Path.home() / ".claude" / "session-digest.local.md"
        hints = [f"  Create {user_path} (all projects)"]
        if cwd:
            project_path = Path(cwd) / ".claude" / "session-digest.local.md"
            hints.append(f"  Or {project_path} (this project only)")
        print(
            "[session-digest] No config found.\n" + "\n".join(hints) + "\n"
            "  Template: $(claude plugin path claude-session-digest)/config.example.md",
            file=sys.stderr,
        )
        return None

    try:
        raw = config_path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        print(
            f"[session-digest] Cannot read config {config_path}: {exc}", file=sys.stderr
        )
        return None

    # Legacy JSON support (SESSION_DIGEST_CONFIG pointing to .json)
    if config_path.suffix == ".json":
        try:
            user_flat = json.loads(raw)
        except json.JSONDecodeError as exc:
            print(f"[session-digest] Config parse error: {exc}", file=sys.stderr)
            return None
        user_config = user_flat
    else:
        # Parse .local.md frontmatter
        user_flat = _parse_frontmatter(raw)
        if not user_flat:
            print(
                f"[session-digest] No frontmatter found in {config_path}",
                file=sys.stderr,
            )
            return None
        user_config = _unflatten_config(user_flat)

    config = _deep_merge(DEFAULTS, user_config)

    # Expand ~ in paths
    config["output_dir"] = str(Path(config["output_dir"]).expanduser())
    obsidian = config["obsidian"]
    if obsidian.get("vault_path"):
        obsidian["vault_path"] = str(Path(obsidian["vault_path"]).expanduser())
    if obsidian.get("template_path"):
        obsidian["template_path"] = str(Path(obsidian["template_path"]).expanduser())

    return config


# ---------------------------------------------------------------------------
# Stdin Parsing
# ---------------------------------------------------------------------------


def read_stdin() -> dict[str, Any]:
    """Read and parse SessionEnd JSON from stdin."""
    try:
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        return {}


# ---------------------------------------------------------------------------
# Transcript Extraction (Phase 2)
# ---------------------------------------------------------------------------


def _parse_user_text(content: Any) -> str:
    """Extract text from user message content (string or list of blocks)."""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text", "").strip())
                elif block.get("type") == "tool_result":
                    # Skip tool result blocks in user messages
                    pass
        return " ".join(p for p in parts if p)
    return ""


def _tool_call_label(name: str, inputs: dict) -> str:
    """Format a tool call as 'ToolName(key_param)' for prompt context."""
    if not inputs:
        return name
    # Pick the most informative parameter
    for key in ("command", "path", "file_path", "pattern", "query", "url", "content"):
        val = inputs.get(key)
        if val and isinstance(val, str):
            # Truncate long values
            short = val[:60].replace("\n", " ")
            return f"{name}({short})"
    # Fallback: first string value
    for val in inputs.values():
        if val and isinstance(val, str):
            short = val[:60].replace("\n", " ")
            return f"{name}({short})"
    return name


def extract_transcript(transcript_path: str) -> dict[str, Any]:
    """
    Parse JSONL transcript and return structured data.

    Returns:
        {
            "user_messages": [str, ...],   # first 15, full text
            "tools_used": [str, ...],       # unique tool names
            "tool_calls": [str, ...],       # tool calls with key params, up to 30
            "files_modified": [str, ...],   # from file-history-snapshot
            "start_ts": float | None,
            "end_ts": float | None,
            "turn_count": int,
        }
    """
    result: dict[str, Any] = {
        "user_messages": [],
        "tools_used": [],
        "tool_calls": [],
        "files_modified": [],
        "start_ts": None,
        "end_ts": None,
        "turn_count": 0,
    }

    if not transcript_path or not Path(transcript_path).exists():
        return result

    tools_seen: set[str] = set()
    tool_calls: list[str] = []
    files_seen: set[str] = set()
    user_messages: list[str] = []
    timestamps: list[float] = []

    try:
        path = Path(transcript_path)
        with path.open(encoding="utf-8", errors="replace") as fh:
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

                msg = entry.get("message", {})
                role = msg.get("role", "")
                content = msg.get("content", [])

                if role == "user":
                    text = _parse_user_text(content)
                    if text:
                        result["turn_count"] += 1
                        if len(user_messages) < 15:
                            user_messages.append(text)

                elif role == "assistant":
                    # Extract tool uses with inputs
                    if isinstance(content, list):
                        for block in content:
                            if (
                                isinstance(block, dict)
                                and block.get("type") == "tool_use"
                            ):
                                name = block.get("name", "")
                                if name:
                                    tools_seen.add(name)
                                    if len(tool_calls) < 30:
                                        inputs = block.get("input", {}) or {}
                                        tool_calls.append(
                                            _tool_call_label(name, inputs)
                                        )
                                    # Extract file_path from Write/Edit tools
                                    if name in ("Write", "Edit", "NotebookEdit"):
                                        inputs = block.get("input", {}) or {}
                                        fpath = inputs.get("file_path") or inputs.get("notebook_path") or ""
                                        if fpath:
                                            files_seen.add(fpath)

                # file-history-snapshot entries
                if entry.get("type") == "file-history-snapshot":
                    # Legacy format: entry.files[]
                    for f in entry.get("files", []):
                        fname = f.get("path") or f.get("filename") or ""
                        if fname:
                            files_seen.add(fname)
                    # Current format: snapshot.trackedFileBackups (dict keys)
                    snapshot = entry.get("snapshot", {})
                    for fpath in snapshot.get("trackedFileBackups", {}):
                        if fpath:
                            files_seen.add(fpath)

    except OSError as exc:
        print(f"[session-digest] Cannot read transcript: {exc}", file=sys.stderr)

    result["user_messages"] = user_messages
    result["tools_used"] = sorted(tools_seen)
    result["tool_calls"] = tool_calls

    # Normalize file paths: prefer relative/short forms, deduplicate
    normalized: set[str] = set()
    for fpath in files_seen:
        name = Path(fpath).name
        # Keep the shortest variant per filename
        existing = {p for p in normalized if Path(p).name == name}
        if existing:
            shortest = min(existing | {fpath}, key=len)
            normalized = (normalized - existing) | {shortest}
        else:
            normalized.add(fpath)
    result["files_modified"] = sorted(normalized)[:20]

    if timestamps:
        result["start_ts"] = min(timestamps)
        result["end_ts"] = max(timestamps)

    return result


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


# ---------------------------------------------------------------------------
# AI Summarization (Phase 3)
# ---------------------------------------------------------------------------

PROMPT_TEMPLATE = """You are analyzing a Claude Code session. The daily note may already contain previous entries shown below.
Based on the messages and context, provide a summary that complements (not duplicates) existing entries.
If this is a resumed session, update the summary to cover ALL work done.

Line 1: Category (one of: feature, bugfix, refactor, research, config, docs, review, other)
Line 2: Summary in 1-2 sentences describing what was accomplished.{language_instruction}

Session context:
- Duration: {duration}
- Turns: {turns}
- Tools used: {tools}
- Files modified: {files}

Messages from user during session:
{messages}{note_context}
Respond in this exact format:
Category: <category>
Summary: <summary>"""


def summarize(
    transcript: dict[str, Any],
    config: dict[str, Any],
    existing_note: str = "",
    duration_secs: float = 0.0,
) -> tuple[str, str]:
    """
    Generate AI summary using claude CLI.

    Returns: (category, summary_text)
    Fallback on failure: ("other", first_user_message or "Session")
    """
    model = config.get("model")
    messages = transcript.get("user_messages", [])
    fallback_summary = messages[0] if messages else "Session"

    if not model or not messages:
        return "other", fallback_summary

    # Build prompt
    msgs_text = "\n".join(f"- {m}" for m in messages)
    note_context = (
        f"\n\nExisting daily note entries:\n{existing_note}"
        if existing_note
        else ""
    )
    language = config.get("language")
    language_instruction = f" Write the summary in {language}." if language else ""

    tool_calls = transcript.get("tool_calls", [])
    tools_used = transcript.get("tools_used", [])
    files_modified = transcript.get("files_modified", [])
    # Use detailed tool_calls if available, otherwise fall back to tool names
    tools_str = ", ".join(tool_calls) if tool_calls else (", ".join(tools_used) if tools_used else "none")
    files_str = ", ".join(files_modified) if files_modified else "none"
    duration_str = format_duration(duration_secs) if duration_secs else "unknown"
    turn_count = transcript.get("turn_count", 0)

    prompt = PROMPT_TEMPLATE.format(
        messages=msgs_text,
        note_context=note_context,
        language_instruction=language_instruction,
        duration=duration_str,
        turns=turn_count,
        tools=tools_str,
        files=files_str,
    )

    # Clean environment — avoid "already running" error
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)
    env.pop("CLAUDE_CODE", None)

    try:
        result = subprocess.run(
            ["claude", "-p", "--model", model],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
        output = result.stdout.strip()
        if not output:
            return "other", fallback_summary

        # Parse response
        category = "other"
        summary = fallback_summary
        for line in output.splitlines():
            line = line.strip()
            if line.lower().startswith("category:"):
                raw_cat = line.split(":", 1)[1].strip().lower()
                valid = {
                    "feature",
                    "bugfix",
                    "refactor",
                    "research",
                    "config",
                    "docs",
                    "review",
                    "other",
                }
                category = raw_cat if raw_cat in valid else "other"
            elif line.lower().startswith("summary:"):
                summary = line.split(":", 1)[1].strip()

        return category, summary

    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        print(
            f"[session-digest] AI summary failed ({exc}), using fallback",
            file=sys.stderr,
        )
        return "other", fallback_summary


# ---------------------------------------------------------------------------
# Dedup Check
# ---------------------------------------------------------------------------


def is_duplicate(file_path: Path, session_id: str) -> bool:
    """Check if session_id HTML comment already exists in file."""
    if not file_path.exists():
        return False
    try:
        content = file_path.read_text(encoding="utf-8")
        return f"<!-- session:{session_id} -->" in content
    except OSError:
        return False


def replace_entry(file_path: Path, session_id: str, new_entry: str) -> None:
    """Replace existing session entry block in file (atomic). Used for resume updates."""
    if not file_path.exists():
        return
    content = file_path.read_text(encoding="utf-8")
    marker = f"<!-- session:{session_id} -->"
    start = content.find(marker)
    if start == -1:
        return

    # Find end of entry block: next session comment, next heading (##/###), or EOF
    after_marker = content[start + len(marker) :]
    candidates: list[int] = []

    next_session = after_marker.find("<!-- session:")
    if next_session != -1:
        candidates.append(next_session)

    next_heading = re.search(r"^#{2,3} ", after_marker, re.MULTILINE)
    if next_heading:
        candidates.append(next_heading.start())

    if candidates:
        block_offset = min(candidates)
        tail = after_marker[block_offset:].lstrip("\n")
    else:
        tail = ""

    _atomic_write(file_path, content[:start] + new_entry + "\n\n" + tail)


# ---------------------------------------------------------------------------
# Entry Formatting
# ---------------------------------------------------------------------------


def format_entry(
    session_id: str,
    start_ts: float | None,
    duration_secs: float,
    category: str,
    summary: str,
    transcript: dict[str, Any],
    config: dict[str, Any],
    branch: str = "",
) -> str:
    """Format a single session entry block."""
    fmt = config.get("daily_format", DEFAULTS["daily_format"])

    # Time
    if start_ts:
        dt = datetime.fromtimestamp(start_ts, tz=UTC).astimezone()
        time_str = dt.strftime("%H:%M")
    else:
        time_str = datetime.now().strftime("%H:%M")

    duration_str = format_duration(duration_secs)

    # Entry header
    entry_tmpl = fmt.get("entry_format", DEFAULTS["daily_format"]["entry_format"])
    try:
        header = entry_tmpl.format(
            time=time_str,
            category=category,
            duration=duration_str,
            tools=len(transcript.get("tools_used", [])),
            branch=branch,
        )
    except KeyError as e:
        print(
            f"[session-digest] Warning: invalid variable {e} in entry_format, using default",
            file=sys.stderr,
        )
        header = DEFAULTS["daily_format"]["entry_format"].format(
            time=time_str,
            category=category,
            duration=duration_str,
            tools=len(transcript.get("tools_used", [])),
            branch=branch,
        )

    lines = [
        f"<!-- session:{session_id} -->",
        header,
        f"> {summary}",
    ]

    if fmt.get("show_branch") and branch:
        lines.append(f"> *Branch: `{branch}`*")

    if fmt.get("show_tools") and transcript.get("tools_used"):
        tools_list = ", ".join(transcript["tools_used"][:8])
        lines.append(f"> *Tools: {tools_list}*")

    if fmt.get("show_files") and transcript.get("files_modified"):
        home = str(Path.home())
        short = [f.replace(home, "~") for f in transcript["files_modified"][:5]]
        files_list = ", ".join(f"`{f}`" for f in short)
        lines.append(f"> *Files: {files_list}*")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Plain Writer (Phase 4)
# ---------------------------------------------------------------------------


def _atomic_write(file_path: Path, content: str) -> None:
    """Write content atomically using tmp file + os.replace."""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=file_path.parent,
        delete=False,
        suffix=".tmp",
    ) as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    os.replace(tmp_path, file_path)


def _find_or_create_project_section(
    content: str,
    project: str,
    project_heading_tmpl: str,
    wikilinks: bool = False,
) -> tuple[str, int]:
    """
    Find or create project heading in content.

    Returns: (updated_content, insert_position_after_heading)
    """
    project_display = f"[[{project}]]" if wikilinks else project
    heading = project_heading_tmpl.format(project=project_display)

    # Look for existing heading (both wikilink and plain variants)
    patterns = [
        re.escape(heading),
        re.escape(project_heading_tmpl.format(project=f"[[{project}]]")),
        re.escape(project_heading_tmpl.format(project=project)),
    ]
    for pat in patterns:
        match = re.search(f"^{pat}$", content, re.MULTILINE)
        if match:
            # Found — find insertion point (after heading + possible blank line, before next ### or ##)
            pos = match.end()
            # Skip blank lines after heading
            while pos < len(content) and content[pos] in ("\n", "\r"):
                pos += 1
            # Find the end of this project's section (next ### or ## heading)
            next_heading = re.search(r"^#{2,3} ", content[pos:], re.MULTILINE)
            if next_heading:
                insert_at = pos + next_heading.start()
            else:
                insert_at = len(content)
            return content, insert_at

    # Not found — append project heading at end of content
    separator = "\n" if content.endswith("\n") else "\n\n"
    content = content + separator + heading + "\n\n"
    return content, len(content)


def write_plain(
    output_dir: str,
    date_str: str,
    project: str,
    entry: str,
    config: dict[str, Any],
) -> Path:
    """Write entry to plain markdown daily file."""
    file_path = Path(output_dir) / f"{date_str}.md"
    fmt = config.get("daily_format", DEFAULTS["daily_format"])
    project_heading_tmpl = fmt.get(
        "project_heading", DEFAULTS["daily_format"]["project_heading"]
    )
    group_by_project = fmt.get("group_by_project", True)

    if not file_path.exists():
        content = f"# Session Digest \u2014 {date_str}\n\n"
    else:
        content = file_path.read_text(encoding="utf-8")

    if group_by_project:
        content, insert_at = _find_or_create_project_section(
            content, project, project_heading_tmpl, wikilinks=False
        )
        # Insert entry at position
        content = content[:insert_at] + entry + "\n\n" + content[insert_at:]
    else:
        if not content.endswith("\n"):
            content += "\n"
        content += "\n" + entry + "\n"

    _atomic_write(file_path, content)
    return file_path


# ---------------------------------------------------------------------------
# Obsidian Writer (Phase 5)
# ---------------------------------------------------------------------------

OBSIDIAN_FRONTMATTER_TEMPLATE = """---
Date: {date}
tags:
  - daily
---

"""


def _read_or_create_obsidian_note(
    note_path: Path,
    date_str: str,
    section_heading: str,
    template_path: str,
) -> str:
    """Read existing note or create new one with frontmatter."""
    if note_path.exists():
        return note_path.read_text(encoding="utf-8")

    # Try user template
    if template_path and Path(template_path).exists():
        tmpl = Path(template_path).read_text(encoding="utf-8")
        return tmpl.replace("{{date}}", date_str).replace("{date}", date_str)

    # Built-in frontmatter
    content = OBSIDIAN_FRONTMATTER_TEMPLATE.format(date=date_str)
    content += section_heading + "\n\n"
    return content


def write_obsidian(
    vault_path: str,
    date_str: str,
    project: str,
    entry: str,
    config: dict[str, Any],
) -> Path:
    """Write entry into Obsidian daily note under the configured section."""
    obs = config["obsidian"]
    daily_dir = obs.get("daily_notes_dir", "Daily notes")
    folder_fmt = obs.get("folder_format", "%Y/%m")
    date_fmt = obs.get("date_format", "%Y-%m-%d")
    section_heading = obs.get("section_heading", "## Notes")
    wikilinks = obs.get("wikilinks", True)
    template_path = obs.get("template_path", "")

    # Build note path
    try:
        dt = datetime.strptime(date_str, date_fmt)
    except ValueError:
        dt = datetime.now()

    folder = dt.strftime(folder_fmt)
    note_filename = f"{date_str}.md"
    note_path = Path(vault_path) / daily_dir / folder / note_filename
    note_path.parent.mkdir(parents=True, exist_ok=True)

    fmt = config.get("daily_format", DEFAULTS["daily_format"])
    project_heading_tmpl = fmt.get(
        "project_heading", DEFAULTS["daily_format"]["project_heading"]
    )

    content = _read_or_create_obsidian_note(
        note_path, date_str, section_heading, template_path
    )

    # Find section heading
    section_match = re.search(f"^{re.escape(section_heading)}$", content, re.MULTILINE)
    if not section_match:
        # Append section at end
        content = content.rstrip() + f"\n\n{section_heading}\n\n"
        section_match = re.search(
            f"^{re.escape(section_heading)}$", content, re.MULTILINE
        )

    # Work within the section
    section_start = section_match.end()

    # Find end of section (next ## heading)
    next_section = re.search(r"^## ", content[section_start:], re.MULTILINE)
    section_end = section_start + next_section.start() if next_section else len(content)

    section_content = content[section_start:section_end]

    # Find or create project sub-heading within section
    section_content, insert_at = _find_or_create_project_section(
        section_content, project, project_heading_tmpl, wikilinks=wikilinks
    )

    # Insert entry
    section_content = (
        section_content[:insert_at] + entry + "\n\n" + section_content[insert_at:]
    )

    content = content[:section_start] + section_content + content[section_end:]
    _atomic_write(note_path, content)
    return note_path


# ---------------------------------------------------------------------------
# Main Orchestration (Phase 6)
# ---------------------------------------------------------------------------


def get_project_name(cwd: str) -> str:
    """Extract project name from working directory path."""
    if not cwd:
        return "unknown"
    return Path(cwd).name or "unknown"


def get_git_branch(cwd: str) -> str:
    """Detect current git branch from cwd. Returns empty string on failure."""
    if not cwd:
        return ""
    try:
        result = subprocess.run(
            ["git", "-C", cwd, "branch", "--show-current"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return ""


def _print_result(icon: str, parts: list[str], color: str = "") -> None:
    """Print result directly to terminal, bypassing hook stdout capture."""
    line = f"{icon} session-digest · " + " · ".join(p for p in parts if p)
    try:
        with open("/dev/tty", "w") as tty:
            use_color = tty.isatty() and not os.environ.get("NO_COLOR")
            if use_color and color:
                tty.write(f"\033[{color}m{line}\033[0m\n")
            else:
                tty.write(line + "\n")
            tty.flush()
    except OSError:
        print(line, file=sys.stderr, flush=True)


def _print_progress(message: str) -> None:
    """Print progress directly to terminal, bypassing hook stdout capture."""
    line = f"⏳ session-digest · {message}"
    try:
        with open("/dev/tty", "w") as tty:
            use_color = tty.isatty() and not os.environ.get("NO_COLOR")
            if use_color:
                tty.write(f"\033[2m{line}\033[0m\n")
            else:
                tty.write(line + "\n")
            tty.flush()
    except OSError:
        print(line, file=sys.stderr, flush=True)


def main() -> None:
    """Main entry point. Reads stdin, processes session, writes digest. Always exits 0."""
    try:
        _run()
    except Exception as exc:
        print(f"[session-digest] Unexpected error: {exc}", file=sys.stderr)
    sys.exit(0)


def _run() -> None:
    """Core processing. May raise — caller handles."""
    # 1. Parse stdin
    data = read_stdin()
    session_id = data.get("session_id", "")
    transcript_path = data.get("transcript_path", "")
    cwd = data.get("cwd", "")

    if not session_id:
        print("[session-digest] No session_id in stdin, skipping", file=sys.stderr)
        return

    # 2. Load config
    config = load_config(cwd)
    if config is None:
        _print_result("○", ["not configured — run /digest-config to set up"])
        return

    # 3. Extract transcript
    _print_progress("processing...")
    transcript = extract_transcript(transcript_path)

    # 4. min_turns check
    min_turns = config.get("min_turns", 3)
    if transcript["turn_count"] < min_turns:
        print(
            f"[session-digest] Session {session_id[:8]}: {transcript['turn_count']} turns < min_turns={min_turns}, skipping",
            file=sys.stderr,
        )
        turn_count = transcript["turn_count"]
        _print_result("○", [f"skipped ({turn_count} turns < min {min_turns})"])
        return

    # 5. Determine output file and check for existing session (resume detection)
    obsidian_cfg = config.get("obsidian", {})
    obsidian_enabled = obsidian_cfg.get("enabled", False)

    if obsidian_enabled:
        vault_path = obsidian_cfg.get("vault_path", "")
        daily_dir = obsidian_cfg.get("daily_notes_dir", "Daily notes")
        folder_fmt = obsidian_cfg.get("folder_format", "%Y/%m")
        date_fmt = obsidian_cfg.get("date_format", "%Y-%m-%d")
        dt = datetime.now()
        date_str = dt.strftime(date_fmt)
        folder = dt.strftime(folder_fmt)
        output_file = Path(vault_path) / daily_dir / folder / f"{date_str}.md"
    else:
        date_str = datetime.now().strftime("%Y-%m-%d")
        output_file = Path(config["output_dir"]) / f"{date_str}.md"

    is_resume = is_duplicate(output_file, session_id)

    # 6. Calculate duration
    start_ts = transcript.get("start_ts")
    end_ts = transcript.get("end_ts")
    duration_secs = (end_ts - start_ts) if (start_ts and end_ts) else 0.0

    # 7. Read existing note for LLM context (helps summarize resumed sessions)
    existing_note = ""
    if output_file.exists():
        try:
            existing_note = output_file.read_text(encoding="utf-8")
        except OSError:
            pass

    # 8. AI summary
    category, summary = summarize(transcript, config, existing_note, duration_secs)

    # 9. Format entry
    project = get_project_name(cwd)
    branch = get_git_branch(cwd)
    entry = format_entry(
        session_id=session_id,
        start_ts=start_ts,
        duration_secs=duration_secs,
        category=category,
        summary=summary,
        transcript=transcript,
        config=config,
        branch=branch,
    )

    # 10. Write to output (update in-place for resume, append for new sessions)
    if is_resume:
        replace_entry(output_file, session_id, entry)
        print(
            f"[session-digest] Updated (resume) {session_id[:8]} in {output_file}",
            file=sys.stderr,
        )
        str_path = str(output_file).replace(str(Path.home()), "~")
        _print_result(
            "↻",
            [category, format_duration(duration_secs) if duration_secs else "", project, f"→ {str_path} (updated)"],
            "33",
        )
        return

    if obsidian_enabled:
        vault_path = obsidian_cfg.get("vault_path", "")
        if not vault_path:
            print(
                "[session-digest] obsidian.enabled=true but vault_path is empty, falling back to plain mode",
                file=sys.stderr,
            )
            out = write_plain(config["output_dir"], date_str, project, entry, config)
        else:
            out = write_obsidian(vault_path, date_str, project, entry, config)
    else:
        out = write_plain(config["output_dir"], date_str, project, entry, config)

    print(f"[session-digest] Written to {out}", file=sys.stderr)
    str_path = str(out).replace(str(Path.home()), "~")
    _print_result(
        "✓",
        [category, format_duration(duration_secs) if duration_secs else "", project, f"→ {str_path}"],
        "32",
    )


if __name__ == "__main__":
    main()
