# claude-session-digest

> On-demand CLI tool + commands for querying Claude Code sessions and writing daily markdown digests.

No hooks, no automatic API calls. You control when digests are generated — via the `/digest` command, the `daily-assistant` agent, or direct CLI queries.

## How It Works

```
/digest command          daily-assistant agent     digest-cli.py (direct)
     │                        │                        │
     ├─ calls CLI             ├─ calls CLI             ├─ list sessions
     ├─ summarizes natively   ├─ analyzes sessions     ├─ show session detail
     ├─ writes to daily note  ├─ morning/summary/...   ├─ list projects
     │                        │                        ├─ list modified files
     │                        │                        └─ show config
     │                        │
     └── session-digest skill teaches format, dedup, CLI usage
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
language: null
min_turns: 3
---
```

### 3. Generate your first digest

```
/digest
```

This lists today's sessions, generates AI summaries (natively, no subprocess), and writes entries to your daily note.

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

### Core Options

| Key | Default | Description |
|-----|---------|-------------|
| `output_dir` | `~/daily-summaries` | Directory for daily `.md` files |
| `language` | `null` | Summary language (e.g. `uk`, `French`, `Українська`). `null` = English |
| `min_turns` | `3` | Skip sessions shorter than N user messages |

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
| `obsidian_template_path` | `""` | Path to template file for new daily notes |

### Format Options

| Key | Default | Description |
|-----|---------|-------------|
| `group_by_project` | `true` | Group entries under project headings |
| `show_files` | `true` | Show modified files list |
| `show_branch` | `true` | Show git branch |
| `show_worktree` | `true` | Show git worktree path |
| `project_heading` | `"### 🤖 {project}"` | Template for project heading |

---

## CLI Tool

The `digest-cli.py` tool queries Claude Code session transcripts from `~/.claude/projects/` and outputs JSON.

```bash
CLI="$(claude plugin path claude-session-digest)/scripts/digest-cli.py"

# List today's sessions
python3 "$CLI" list --date today

# List sessions for a specific date
python3 "$CLI" list --date 2026-02-24 --min-turns 3

# Show full session detail (dialog, files)
python3 "$CLI" show <session-id-or-prefix>

# List projects with session counts
python3 "$CLI" projects --since 2026-02-01

# List modified files
python3 "$CLI" files --date today --project myproject

# Show resolved config
python3 "$CLI" config
```

Session metadata is cached at `~/.cache/digest-cli/sessions.json` (invalidated by mtime+size changes).

---

## Commands

### `/digest`

Generate and write session digest entries into your daily note. Lists sessions, checks for existing entries (dedup), generates summaries, and writes formatted entries.

### `/digest-init`

Interactive setup wizard. Guides you through configuring output mode, path, and language.

### `/digest-config`

Shows your current configuration and resolved config path.

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

---

## Daily Assistant Agent

The `daily-assistant` agent uses the CLI tool to query sessions:

- **Morning** — queries yesterday's sessions, shows carry-over context
- **Evening** — queries today's sessions for day summary; suggests `/digest` if entries missing
- **Analysis** — aggregates sessions by project and time period
- **Notes** — quick notes with context from your vault

---

## Breaking Changes from v0.4

v1.0 removes the automatic SessionEnd hook. Sessions are no longer summarized automatically — use `/digest` to generate entries on demand. This eliminates API calls after every session and removes the delay on session exit.

**Removed:**
- `hooks/hooks.json` — no more SessionEnd hook
- `scripts/session-digest.py` — replaced by `digest-cli.py`
- `model` config key — summaries are generated natively by the agent
- `quiet` config key — no more terminal output to suppress

**Added:**
- `scripts/digest-cli.py` — CLI tool for querying sessions
- `commands/digest.md` — `/digest` command for on-demand entry generation
- `skills/session-digest/SKILL.md` — skill teaching entry format and CLI usage

---

## Platform Notes

**Windows:** `~` in paths means `%USERPROFILE%` (e.g. `C:\Users\you`). In PowerShell, `~` expands automatically. In cmd.exe, use `%USERPROFILE%\.claude\session-digest.local.md` instead.

---

## License

Apache 2.0 — free to use and fork, attribution required.
