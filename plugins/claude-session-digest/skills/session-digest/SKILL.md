---
name: session-digest
description: Teaches any agent how to work with Claude Code session transcripts, write daily digest entries, and use the digest-cli tool. Use when writing session summaries, generating daily notes, or analyzing past sessions.
---

# Session Digest Skill

This skill teaches you how to query Claude Code sessions and write structured digest entries into daily notes.

## CLI Tool

The `digest-cli.py` tool lives at `${CLAUDE_PLUGIN_ROOT}/scripts/digest-cli.py`. It outputs JSON to stdout.

### Commands

```bash
# List sessions (filterable)
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/digest-cli.py" list [--date today|yesterday|YYYY-MM-DD] [--project NAME] [--since YYYY-MM-DD] [--min-turns N]

# Show full session detail (dialog, files, messages)
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/digest-cli.py" show <session-id-or-prefix>

# List projects with session counts
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/digest-cli.py" projects [--since YYYY-MM-DD]

# List modified files across sessions
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/digest-cli.py" files [--date DATE] [--project NAME]

# Show resolved configuration
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/digest-cli.py" config
```

### Output Formats

**`list` returns:**
```json
{
  "sessions": [{
    "id": "uuid", "project": "name", "project_path": "/path",
    "date": "2026-02-24", "start_time": "16:09", "end_time": "21:49",
    "duration": "5h 40m", "duration_seconds": 20419,
    "turn_count": 22, "git_branch": "main"
  }]
}
```

**`show` adds:**
```json
{
  "user_messages": ["first message", "second message"],
  "files_modified": ["/path/to/file.py"],
  "dialog": "User: ...\nAssistant: ..."
}
```

## Entry Format

### Categories

One of: `feature`, `bugfix`, `refactor`, `research`, `config`, `docs`, `review`, `debug`, `testing`, `deploy`, `other`

### Deduplication Markers

Every entry MUST have these HTML comments for deduplication:
- `<!-- session:UUID -->` — unique session identifier
- `<!-- title:Short description -->` — hidden title for summary generation

### Obsidian Mode (callout blocks)

```markdown
### 🤖 [[project-name]]

<!-- session:abc123-def4-5678 -->
<!-- title:Implemented auth flow for login page -->
> [!bot]- **09:15** feature · 45m
> - Implemented JWT authentication in `auth.py`
> - Added login/logout endpoints
>
> **Key decisions:** chose JWT over sessions for stateless API
>
> `claude --resume abc123-def4-5678`
> *Branch: `main` · Files: `auth.py`, `routes.py`*
```

### Plain Mode (blockquotes)

```markdown
### 🤖 project-name

<!-- session:abc123-def4-5678 -->
<!-- title:Implemented auth flow for login page -->
**09:15** feature · 45m

> - Implemented JWT authentication in `auth.py`
> - Added login/logout endpoints
>
> **Key decisions:** chose JWT over sessions for stateless API
>
> `claude --resume abc123-def4-5678`
> *Branch: `main` · Files: `auth.py`, `routes.py`*
```

### Project Grouping

When `group_by_project: true`, entries are grouped under project headings:
- Obsidian mode: `### 🤖 [[project-name]]` (wikilinks)
- Plain mode: `### 🤖 project-name`

Use the `project_heading` config template: `### 🤖 {project}`

## Writing Entries

### Summarization Guidelines

When you have the `dialog` field from `show`, generate a summary with:

1. **Category** — pick the most fitting from the list above
2. **Title** — 5-10 word description of what was accomplished
3. **Body** — structured markdown:
   - Bullet list of what was done (always)
   - `**Key decisions:**` only if meaningful choices were made
   - `**Problems:**` only if real blockers encountered
   - `**TODO:** - [ ]` checkboxes for unfinished work
4. **Resume command** — `claude --resume {session_id}`
5. **Metadata** — branch and modified files from session data

Be specific: mention file names, function names, concrete changes.

### Daily Note Structure

Sessions go under the configured `section_heading` (default: `## Notes`).

```markdown
## Notes

### Done
- Implemented auth flow
- Fixed login bug

### 🤖 [[project-a]]

<!-- session:... -->
...entries...

### 🤖 [[project-b]]

<!-- session:... -->
...entries...
```

The `### Done` summary section lists all `<!-- title:... -->` values as bullets.

### Resume Detection

If `<!-- session:UUID -->` already exists in the daily note, this is a **resumed session**. Replace the existing entry block instead of appending a new one.

### Config Access

Use `digest-cli.py config` to get:
- `obsidian.enabled` — determines entry format (callout vs blockquote)
- `obsidian.vault_path` — vault root
- `obsidian.daily_notes_dir` — folder for daily notes
- `obsidian.date_format` — note filename format
- `obsidian.folder_format` — subfolder structure
- `obsidian.section_heading` — heading to insert under
- `obsidian.wikilinks` — use `[[project]]` in headings
- `daily_format.project_heading` — template for project headings
- `daily_format.show_files` / `show_branch` — what metadata to include
- `language` — output language for summaries
