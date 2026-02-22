#!/usr/bin/env python3
"""
claude-session-digest — SessionEnd hook script

Reads SessionEnd JSON from stdin, extracts transcript data, generates an AI summary
(optional), and writes a structured entry to a daily markdown digest.

Config cascade (first found wins):
  1. SESSION_DIGEST_CONFIG env var       — explicit override
  2. {cwd}/.claude/session-digest.local.md  — per-project override
  3. ~/.claude/session-digest.local.md      — user-level defaults

Two output modes:
  - Plain mode (obsidian.enabled=false): clean markdown, compact blockquote metadata
  - Obsidian mode (obsidian.enabled=true): callout blocks, wikilinks, summary

Obsidian Syntax Reference:
  - Callouts: > [!bot]- title — collapsible session entry blocks
  - Wikilinks: [[project]] — clickable link to project note
  - HTML comments: <!-- --> — invisible in reading view, used for session markers
  - Frontmatter: YAML between --- markers, tags: plural, aliases: plural

Docs:   https://github.com/djimontyp/djdev-workshop/tree/main/plugins/claude-session-digest
"""

from __future__ import annotations

import fcntl
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
    "model": "sonnet",
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
    "quiet": False,
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
        "show_files",
        "show_branch",
        "show_worktree",
        "daily_summary",
        "summary_heading",
        "min_duration",
        "project_heading",
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



def count_turns_fast(transcript_path: str) -> int:
    """Quick turn count without full transcript extraction.

    Scans JSONL for user messages only, skipping tool call / file / timestamp
    extraction.  Used for early min_turns gating so we can skip expensive
    parsing for very short sessions.
    """
    if not transcript_path or not Path(transcript_path).exists():
        return 0
    count = 0
    try:
        with Path(transcript_path).open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                # Quick string pre-filter: skip lines that can't contain a user message
                if '"user"' not in line:
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


def _parse_assistant_text(content: Any) -> str:
    """Extract text from assistant message content (list of blocks)."""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", "").strip())
        return " ".join(p for p in parts if p)
    return ""


def extract_transcript(transcript_path: str) -> dict[str, Any]:
    """
    Parse JSONL transcript and return structured data.

    Returns:
        {
            "user_messages": [str, ...],    # all user messages
            "files_modified": [str, ...],   # from file-history-snapshot + Write/Edit
            "full_messages": str,           # formatted dialog for LLM
            "start_ts": float | None,
            "end_ts": float | None,
            "turn_count": int,
        }
    """
    result: dict[str, Any] = {
        "user_messages": [],
        "files_modified": [],
        "full_messages": "",
        "start_ts": None,
        "end_ts": None,
        "turn_count": 0,
    }

    if not transcript_path or not Path(transcript_path).exists():
        return result

    files_seen: set[str] = set()
    user_messages: list[str] = []
    dialog_parts: list[str] = []
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
                        user_messages.append(text)
                        dialog_parts.append(f"User: {text}")

                elif role == "assistant":
                    # Extract text content for dialog
                    text = _parse_assistant_text(content)
                    if text:
                        # Truncate very long assistant responses for context
                        if len(text) > 500:
                            text = text[:500] + "..."
                        dialog_parts.append(f"Assistant: {text}")

                    # Extract file_path from Write/Edit tools
                    if isinstance(content, list):
                        for block in content:
                            if (
                                isinstance(block, dict)
                                and block.get("type") == "tool_use"
                            ):
                                name = block.get("name", "")
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
    result["full_messages"] = "\n".join(dialog_parts)

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
    result["files_modified"] = sorted(normalized)

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

VALID_CATEGORIES = {
    "feature", "bugfix", "refactor", "research", "config",
    "docs", "review", "debug", "testing", "deploy", "other",
}

SYSTEM_PROMPT = """You are a session analyst for Claude Code. Analyze the transcript and produce a structured summary.

First line: category (one of: feature, bugfix, refactor, research, config, docs, review, debug, testing, deploy, other)
Second line: short title (5-10 words)
Rest: markdown body with the following sections (include only relevant ones):

- Bullet list of what was done (always include)
- **Key decisions:** only if meaningful choices were made
- **Problems:** only if real blockers encountered
- **TODO:** `- [ ]` checkboxes for unfinished work

Be specific: mention file names, function names, concrete changes.
Do NOT include metadata (branch, files, time, tools) — that's added automatically."""


def summarize(
    transcript: dict[str, Any],
    config: dict[str, Any],
    existing_note: str = "",
    duration_secs: float = 0.0,
) -> tuple[str, str, str]:
    """
    Generate AI summary using claude CLI.

    Returns: (category, title, body)
    Fallback on failure: ("other", first_user_message or "Session", "")
    """
    model = config.get("model")
    messages = transcript.get("user_messages", [])
    fallback_title = messages[0][:80] if messages else "Session"

    if not model or not messages:
        return "other", fallback_title, ""

    # Build system prompt with optional language instruction
    language = config.get("language")
    system_prompt = SYSTEM_PROMPT
    if language:
        system_prompt += f"\nWrite in {language}."

    # Build user prompt with full transcript
    files_modified = transcript.get("files_modified", [])
    files_str = ", ".join(files_modified) if files_modified else "none"
    duration_str = format_duration(duration_secs) if duration_secs else "unknown"
    turn_count = transcript.get("turn_count", 0)
    full_messages = transcript.get("full_messages", "")
    project = transcript.get("project", "unknown")

    note_context = ""
    if existing_note:
        note_context = f"\n\nExisting daily note entries (avoid duplicating):\n{existing_note}"

    user_prompt = f"""Session context:
- Project: {project}
- Duration: {duration_str}
- Turns: {turn_count}
- Files modified: {files_str}

Full session transcript:
{full_messages}{note_context}"""

    # Clean environment — avoid "already running" error
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)
    env.pop("CLAUDE_CODE", None)

    try:
        result = subprocess.run(
            ["claude", "-p", "--model", model, "--output-format", "json",
             "--append-system-prompt", system_prompt],
            input=user_prompt,
            capture_output=True,
            text=True,
            timeout=45,
            env=env,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return "other", fallback_title, ""

        # Parse JSON response from --output-format json
        try:
            response = json.loads(result.stdout)
            text = response.get("result", result.stdout).strip()
        except json.JSONDecodeError:
            text = result.stdout.strip()

        if not text:
            return "other", fallback_title, ""

        # Parse: line 1 = category, line 2 = title, rest = body
        lines = text.split("\n", 2)
        raw_cat = lines[0].strip().lower() if lines else "other"
        # Strip common LLM prefixes: "Category: feature", "**feature**", "# feature"
        raw_cat = re.sub(r"^(category|cat)\s*:\s*", "", raw_cat).strip("*#` ").strip()
        category = raw_cat if raw_cat in VALID_CATEGORIES else "other"
        title = lines[1].strip() if len(lines) > 1 else fallback_title
        body = lines[2].strip() if len(lines) > 2 else ""

        return category, title, body

    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        print(
            f"[session-digest] AI summary failed ({exc}), using fallback",
            file=sys.stderr,
        )
        return "other", fallback_title, ""


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


def replace_entry(
    file_path: Path,
    session_id: str,
    new_entry: dict[str, str],
    config: dict[str, Any] | None = None,
) -> None:
    """Replace existing session entry block in file (atomic, locked). Used for resume updates."""
    if not file_path.exists():
        return

    lock_path = file_path.with_suffix(".md.lock")
    with open(lock_path, "w") as lock_fh:
        fcntl.flock(lock_fh, fcntl.LOCK_EX)
        try:
            content = file_path.read_text(encoding="utf-8")
            marker = f"<!-- session:{session_id} -->"
            start = content.find(marker)
            if start == -1:
                return

            # Find end of entry block: next session comment or next heading (##/###)
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

            content = content[:start] + new_entry["entry_block"] + "\n\n" + tail

            # Regenerate summary if Obsidian mode
            if config:
                fmt = config.get("daily_format", {})
                obs_cfg = config.get("obsidian", {})
                if obs_cfg.get("enabled") and fmt.get("daily_summary"):
                    section_heading = obs_cfg.get("section_heading", "## Notes")
                    summary_heading = fmt.get("summary_heading", DEFAULTS["daily_format"]["summary_heading"])
                    sec_match = re.search(
                        f"^{re.escape(section_heading)}$", content, re.MULTILINE
                    )
                    if sec_match:
                        sec_start = sec_match.end()
                        next_sec = re.search(r"^## ", content[sec_start:], re.MULTILINE)
                        sec_end = sec_start + next_sec.start() if next_sec else len(content)
                        sec_content = content[sec_start:sec_end]
                        new_sec = _regenerate_summary(sec_content, summary_heading)
                        content = content[:sec_start] + new_sec + content[sec_end:]

            _atomic_write(file_path, content)
        finally:
            fcntl.flock(lock_fh, fcntl.LOCK_UN)
    try:
        lock_path.unlink(missing_ok=True)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Entry Formatting
# ---------------------------------------------------------------------------


def format_entry(
    session_id: str,
    start_ts: float | None,
    duration_secs: float,
    category: str,
    title: str,
    body: str,
    transcript: dict[str, Any],
    config: dict[str, Any],
    branch: str = "",
    worktree: str = "",
    obsidian_mode: bool = False,
) -> dict[str, str]:
    """Format a single session entry as callout (Obsidian) or blockquote (plain).

    Returns dict with keys: marker, title_comment, summary_bullet, entry_block.
    """
    fmt = config.get("daily_format", DEFAULTS["daily_format"])

    # Time
    if start_ts:
        dt = datetime.fromtimestamp(start_ts, tz=UTC).astimezone()
        time_str = dt.strftime("%H:%M")
    else:
        time_str = datetime.now().strftime("%H:%M")

    duration_str = format_duration(duration_secs)

    marker = f"<!-- session:{session_id} -->"
    title_comment = f"<!-- title:{title} -->"
    summary_bullet = f"- {title}"

    # Build metadata line
    home = str(Path.home())
    meta_parts: list[str] = []
    if fmt.get("show_branch") and branch:
        meta_parts.append(f"Branch: `{branch}`")
    if fmt.get("show_worktree") and worktree:
        meta_parts.append(f"Worktree: `{worktree}`")
    if fmt.get("show_files") and transcript.get("files_modified"):
        short = [f.replace(home, "~") for f in transcript["files_modified"][:5]]
        files_list = ", ".join(f"`{f}`" for f in short)
        meta_parts.append(f"Files: {files_list}")
    meta_line = " · ".join(meta_parts) if meta_parts else ""

    # Build body lines
    body_lines: list[str] = []
    if body:
        body_lines.extend(body.splitlines())

    # Add resume command
    body_lines.append("")
    body_lines.append(f"`claude --resume {session_id}`")

    # Add metadata line
    if meta_line:
        body_lines.append(f"*{meta_line}*")

    if obsidian_mode:
        # --- Obsidian mode: callout block ---
        header = f"> [!bot]- **{time_str}** {category} · {duration_str}"
        lines = [marker, title_comment, header]
        for bl in body_lines:
            lines.append(f"> {bl}" if bl else ">")
        entry_block = "\n".join(lines)
    else:
        # --- Plain mode: heading + blockquote body ---
        header = f"**{time_str}** {category} · {duration_str}"
        lines = [marker, title_comment, header, ""]
        for bl in body_lines:
            lines.append(f"> {bl}" if bl else ">")
        entry_block = "\n".join(lines)

    return {
        "marker": marker,
        "title_comment": title_comment,
        "summary_bullet": summary_bullet,
        "entry_block": entry_block,
    }


def _regenerate_summary(section_content: str, summary_heading: str) -> str:
    """Parse session entries and rebuild summary bullet list under summary_heading."""
    # Extract titles from <!-- title:... --> comments
    pattern = r"<!-- title:(.+?) -->"
    summaries: list[str] = []
    for m in re.finditer(pattern, section_content):
        text = m.group(1).strip()
        if text and text not in summaries:
            summaries.append(text)

    if not summaries:
        return section_content

    bullet_list = "\n".join(f"- {s}" for s in summaries)
    heading_esc = re.escape(summary_heading)

    # Find existing summary section
    existing = re.search(
        rf"^{heading_esc}\s*\n((?:- .+\n?)*)",
        section_content,
        re.MULTILINE,
    )
    if existing:
        # Replace existing bullet list
        return (
            section_content[: existing.start(1)]
            + bullet_list
            + "\n"
            + section_content[existing.end(1) :]
        )

    # No existing summary — insert after first blank line (before project headings)
    # Find first ### heading (project heading)
    first_project = re.search(r"^### ", section_content, re.MULTILINE)
    if first_project:
        insert_at = first_project.start()
        return (
            section_content[:insert_at]
            + summary_heading
            + "\n"
            + bullet_list
            + "\n\n"
            + section_content[insert_at:]
        )

    # Fallback: prepend
    return "\n" + summary_heading + "\n" + bullet_list + "\n\n" + section_content



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
            # Found — find insertion point (after heading + possible blank line,
            # before next ### or ## heading)
            pos = match.end()
            # Skip blank lines after heading
            while pos < len(content) and content[pos] in ("\n", "\r"):
                pos += 1
            # Find the end of this project's section
            next_heading = re.search(r"^#{2,3} ", content[pos:], re.MULTILINE)
            insert_at = pos + next_heading.start() if next_heading else len(content)
            return content, insert_at

    # Not found — append project heading at end of content
    separator = "\n" if content.endswith("\n") else "\n\n"
    content = content + separator + heading + "\n\n"
    return content, len(content)


def write_plain(
    output_dir: str,
    date_str: str,
    project: str,
    entry: dict[str, str],
    config: dict[str, Any],
) -> Path:
    """Write entry to plain markdown daily file (with file locking)."""
    file_path = Path(output_dir) / f"{date_str}.md"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    fmt = config.get("daily_format", DEFAULTS["daily_format"])
    project_heading_tmpl = fmt.get(
        "project_heading", DEFAULTS["daily_format"]["project_heading"]
    )
    group_by_project = fmt.get("group_by_project", True)

    lock_path = file_path.with_suffix(".md.lock")
    with open(lock_path, "w") as lock_fh:
        fcntl.flock(lock_fh, fcntl.LOCK_EX)
        try:
            if not file_path.exists():
                content = f"# Session Digest \u2014 {date_str}\n\n"
            else:
                content = file_path.read_text(encoding="utf-8")

            entry_text = entry["entry_block"]

            if group_by_project:
                content, insert_at = _find_or_create_project_section(
                    content, project, project_heading_tmpl, wikilinks=False
                )
                content = content[:insert_at] + entry_text + "\n\n" + content[insert_at:]
            else:
                if not content.endswith("\n"):
                    content += "\n"
                content += "\n" + entry_text + "\n"

            _atomic_write(file_path, content)
        finally:
            fcntl.flock(lock_fh, fcntl.LOCK_UN)
    # Clean up lock file
    try:
        lock_path.unlink(missing_ok=True)
    except OSError:
        pass
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
    entry: dict[str, str],
    config: dict[str, Any],
) -> Path:
    """Write entry into Obsidian daily note under the configured section (with file locking)."""
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

    lock_path = note_path.with_suffix(".md.lock")
    with open(lock_path, "w") as lock_fh:
        fcntl.flock(lock_fh, fcntl.LOCK_EX)
        try:
            content = _read_or_create_obsidian_note(
                note_path, date_str, section_heading, template_path
            )

            # Find section heading
            section_match = re.search(f"^{re.escape(section_heading)}$", content, re.MULTILINE)
            if not section_match:
                content = content.rstrip() + f"\n\n{section_heading}\n\n"
                section_match = re.search(
                    f"^{re.escape(section_heading)}$", content, re.MULTILINE
                )

            section_start = section_match.end()

            # Find end of section (next ## heading)
            next_section = re.search(r"^## ", content[section_start:], re.MULTILINE)
            section_end = section_start + next_section.start() if next_section else len(content)

            section_content = content[section_start:section_end]

            # Find or create project sub-heading within section
            section_content, insert_at = _find_or_create_project_section(
                section_content, project, project_heading_tmpl, wikilinks=wikilinks
            )

            # Insert entry_block
            section_content = (
                section_content[:insert_at]
                + entry["entry_block"]
                + "\n\n"
                + section_content[insert_at:]
            )

            # Rebuild full content with updated section
            content = content[:section_start] + section_content + content[section_end:]

            # Regenerate summary (Obsidian mode)
            if fmt.get("daily_summary"):
                summary_heading = fmt.get("summary_heading", DEFAULTS["daily_format"]["summary_heading"])
                # Recalculate section boundaries after insertion
                next_section = re.search(r"^## ", content[section_start:], re.MULTILINE)
                section_end = (
                    section_start + next_section.start() if next_section else len(content)
                )
                section_content = content[section_start:section_end]
                new_section = _regenerate_summary(section_content, summary_heading)
                content = content[:section_start] + new_section + content[section_end:]

            _atomic_write(note_path, content)
        finally:
            fcntl.flock(lock_fh, fcntl.LOCK_UN)
    # Clean up lock file
    try:
        lock_path.unlink(missing_ok=True)
    except OSError:
        pass
    return note_path


# ---------------------------------------------------------------------------
# Main Orchestration (Phase 6)
# ---------------------------------------------------------------------------


def get_project_name(cwd: str) -> str:
    """Extract project name from working directory path."""
    if not cwd:
        return "unknown"
    return Path(cwd).name or "unknown"


def get_git_worktree(cwd: str) -> str:
    """Detect if cwd is a git worktree. Returns worktree path or empty string."""
    if not cwd:
        return ""
    try:
        git_dir = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--git-dir"],
            capture_output=True, text=True, timeout=5,
        )
        common_dir = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--git-common-dir"],
            capture_output=True, text=True, timeout=5,
        )
        if git_dir.returncode != 0 or common_dir.returncode != 0:
            return ""
        gd = Path(git_dir.stdout.strip()).resolve()
        cd = Path(common_dir.stdout.strip()).resolve()
        if gd != cd:
            # This is a worktree — get its toplevel
            toplevel = subprocess.run(
                ["git", "-C", cwd, "rev-parse", "--show-toplevel"],
                capture_output=True, text=True, timeout=5,
            )
            if toplevel.returncode == 0:
                wt_path = toplevel.stdout.strip()
                # Shorten home prefix to ~/
                home = str(Path.home())
                if wt_path.startswith(home):
                    wt_path = "~" + wt_path[len(home):]
                return wt_path
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return ""


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
        _print_result("○", ["not configured — run /digest-init to set up"])
        return

    quiet = config.get("quiet", False)

    # 3. Quick min_turns gate (avoids expensive full transcript parsing)
    min_turns = config.get("min_turns", 3)
    turn_count = count_turns_fast(transcript_path)
    if turn_count < min_turns:
        print(
            f"[session-digest] Session {session_id[:8]}: {turn_count} turns < min_turns={min_turns}, skipping",
            file=sys.stderr,
        )
        if not quiet:
            _print_result("○", [f"skipped ({turn_count} turns < min {min_turns})"])
        return

    # 4. Extract transcript (full parsing)
    if not quiet:
        _print_progress("processing...")
    transcript = extract_transcript(transcript_path)

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

    # 6a. min_duration check
    min_dur = config.get("daily_format", {}).get("min_duration", 0)
    if min_dur and duration_secs < min_dur:
        if not quiet:
            _print_result("○", [f"skipped ({format_duration(duration_secs)} < min)"])
        return

    # 7. Read existing note for LLM context (helps summarize resumed sessions)
    existing_note = ""
    if output_file.exists():
        try:
            existing_note = output_file.read_text(encoding="utf-8")
        except OSError:
            pass

    # 8. AI summary
    # Add project info to transcript for summarize() prompt
    project = get_project_name(cwd)
    transcript["project"] = project
    category, title, body = summarize(transcript, config, existing_note, duration_secs)

    # 9. Format entry
    branch = get_git_branch(cwd)
    worktree = get_git_worktree(cwd)

    entry = format_entry(
        session_id=session_id,
        start_ts=start_ts,
        duration_secs=duration_secs,
        category=category,
        title=title,
        body=body,
        transcript=transcript,
        config=config,
        branch=branch,
        worktree=worktree,
        obsidian_mode=obsidian_enabled,
    )

    # 10. Write to output (update in-place for resume, append for new sessions)
    if is_resume:
        replace_entry(output_file, session_id, entry, config=config)
        print(
            f"[session-digest] Updated (resume) {session_id[:8]} in {output_file}",
            file=sys.stderr,
        )
        if not quiet:
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
    if quiet:
        _print_result("✓", [f"→ {str_path}"], "32")
    else:
        _print_result(
            "✓",
            [category, format_duration(duration_secs) if duration_secs else "", project, f"→ {str_path}"],
            "32",
        )


if __name__ == "__main__":
    main()
