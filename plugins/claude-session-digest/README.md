# claude-session-digest

> Auto-summarize Claude Code sessions into daily markdown digests.

Automatically captures what you worked on, generates a structured AI summary (Sonnet), and writes daily notes — either as plain markdown files or into your Obsidian vault with collapsible callout blocks.

## How It Works

```mermaid
flowchart TD
    A[SessionEnd hook fires] --> B[Read stdin JSON]
    B --> C[Load config]
    C --> D[Extract transcript]
    D --> E{turns >= min_turns?}
    E -->|No| F[Skip — too short]
    E -->|Yes| G{session_id exists in file?}
    G -->|Yes — resume| H[Read existing note for context]
    G -->|No — new| H
    H --> I[AI summarize — structured body]
    I --> J[Format callout/blockquote entry]
    G -->|Yes| K[Replace existing entry]
    G -->|No| L[Append new entry]
    J --> K
    J --> L
    K --> M[Done — updated]
    L --> M
```

---

## Quick Start

### 1. Install the plugin

```bash
claude plugin install djimontyp/djdev-workshop/claude-session-digest
```

### 2. Run the setup wizard

```
/digest-init
```

Or create your config manually:

```bash
cp "$(claude plugin path claude-session-digest)/config.example.md" ~/.claude/session-digest.local.md
```

Edit `~/.claude/session-digest.local.md` and set your `output_dir`:

```yaml
---
output_dir: ~/Documents/daily-summaries
model: sonnet
min_turns: 3
---
```

### 3. That's it

Next time you end a Claude Code session, a summary entry appears in your daily file.

---

## Configuration

Config uses the `.claude/session-digest.local.md` format — YAML frontmatter in a markdown file.

> **Parser limitations:** The config parser supports only flat `key: value` pairs. YAML lists, nested keys, and multi-line strings are silently ignored. Stick to simple scalar values.

**Config cascade (first found wins):**

| Priority | Path | Scope |
|----------|------|-------|
| 1 | `SESSION_DIGEST_CONFIG` env var | explicit override |
| 2 | `{project}/.claude/session-digest.local.md` | per-project |
| 3 | `~/.claude/session-digest.local.md` | all projects |

**Recommended setup:** create `~/.claude/session-digest.local.md` once — it applies to all your projects automatically. Add a project-level file only when you need to override something for a specific repo.

**This file is never overwritten by plugin updates.**

### Core Options

| Key | Default | Description |
|-----|---------|-------------|
| `output_dir` | `~/daily-summaries` | Directory for daily `.md` files |
| `language` | `null` | Summary language (e.g. `uk`, `French`, `Українська`). `null` = no instruction, LLM defaults to English |
| `model` | `"sonnet"` | AI model for summaries. `null` = offline mode (no API calls). See model options below |
| `min_turns` | `3` | Skip sessions shorter than N user messages |
| `quiet` | `false` | Suppress progress output, show only result path |

### Model Options

`"sonnet"` (default) · `"haiku"` · `"opus"` · `null` (offline — no API calls)

### Obsidian Integration

| Key | Default | Description |
|-----|---------|-------------|
| `obsidian_enabled` | `false` | Write to Obsidian vault instead of plain files |
| `obsidian_vault_path` | `""` | Absolute path to your vault root |
| `obsidian_daily_notes_dir` | `"Daily notes"` | Folder within vault for daily notes |
| `obsidian_date_format` | `"%Y-%m-%d"` | Date format for note filename |
| `obsidian_folder_format` | `"%Y/%m"` | Subfolder structure within daily notes dir |
| `obsidian_section_heading` | `"## Notes"` | Heading under which to insert session entries |
| `obsidian_wikilinks` | `true` | Use `[[project]]` wikilinks in project headings |
| `obsidian_template_path` | `""` | Path to template file for new daily notes. Supports `{{date}}` placeholder |

When `obsidian_enabled: true`, sessions are written into your Obsidian vault daily notes instead of plain files. The plugin inserts entries under `obsidian_section_heading`, grouped by project.

### Format Options

| Key | Default | Description |
|-----|---------|-------------|
| `group_by_project` | `true` | Group entries under project headings |
| `show_files` | `true` | Show modified files list |
| `show_branch` | `true` | Show git branch |
| `show_worktree` | `true` | Show git worktree path |
| `project_heading` | `"### 🤖 {project}"` | Template for project heading. `{project}` = dir name |

---

## Output Format

### Obsidian Mode (callout blocks)

```markdown
### 🤖 [[my-project]]

<!-- session:abc123 -->
<!-- title:Implemented auth flow for login page -->
> [!bot]- **09:15** feature · 45m
> - Implemented JWT authentication in `auth.py`
> - Added login/logout endpoints
>
> **Key decisions:** chose JWT over sessions
>
> `claude --resume abc123-def4-...`
> *Branch: `main` · Files: `auth.py`, `routes.py`*
```

### Plain Mode (blockquote body)

```markdown
### 🤖 my-project

<!-- session:abc123 -->
<!-- title:Implemented auth flow for login page -->
**09:15** feature · 45m

> - Implemented JWT authentication in `auth.py`
> - Added login/logout endpoints
>
> **Key decisions:** chose JWT over sessions
>
> `claude --resume abc123-def4-...`
> *Branch: `main` · Files: `auth.py`, `routes.py`*
```

Each entry includes:
- **Structured summary** — bullet points of what was done, key decisions, problems, TODOs
- **Resume command** — `claude --resume {session_id}` to continue where you left off
- **Metadata** — branch, modified files

---

## Commands

### `/digest-init`

Interactive setup wizard. Guides you through configuring output mode, path, language, and model.

### `/digest-config`

Shows your current configuration and resolved config path.

---

## Daily Assistant Agent

This plugin includes a `daily-assistant` agent that knows about your Claude sessions and daily notes:

- **Morning** — shows yesterday's sessions, reminds of unfinished work
- **Evening** — includes sessions in day review
- **Analysis** — aggregates sessions by project, shows statistics
- **Notes** — quick notes with context from your vault

The agent reads from `~/.claude/session-digest.local.md` — no hardcoded paths.

---

## API Usage

**This plugin makes an API call after EVERY Claude Code session** (when `model` is not `null`).

When a session ends, the script calls `claude -p --model sonnet` to generate a summary. This means:

- **Each session = 1 additional API request** to Sonnet
- On Pro/Max subscription — usually within quota
- On API billing these are **real costs** per call

**To disable AI summarization** (offline mode, no API calls):

```yaml
model: null
```

This uses the first user message as the session title instead.

---

## Troubleshooting

**No entries appearing?**

1. Check config exists: `cat ~/.claude/session-digest.local.md`
2. Check output dir exists: `ls ~/daily-summaries` (or your configured path)
3. Was the session long enough? Check `min_turns` setting
4. Run manually to test:

```bash
TRANSCRIPT=$(ls ~/.claude/projects/*/*.jsonl 2>/dev/null | tail -1)
echo "{\"session_id\":\"test\",\"transcript_path\":\"$TRANSCRIPT\",\"cwd\":\"$(pwd)\",\"reason\":\"user_exit\",\"hook_event_name\":\"SessionEnd\"}" | \
  python3 "$(claude plugin path claude-session-digest)/scripts/session-digest.py"
```

**AI summaries not working?**

Set `model: null` to use offline mode (first user message as description).

**Too much terminal output?**

Set `quiet: true` to suppress progress messages.

---

## Platform Notes

**Windows:** `~` in paths means `%USERPROFILE%` (e.g. `C:\Users\you`). In PowerShell, `~` expands automatically. In cmd.exe, use `%USERPROFILE%\.claude\session-digest.local.md` instead.

---

## License

Apache 2.0 — free to use and fork, attribution required.
