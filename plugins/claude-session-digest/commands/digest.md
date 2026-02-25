---
description: Write session digest entries into your daily note
---

# /digest

Generate and write Claude Code session summaries into your daily note.

## Usage

```
/digest
/digest today
/digest yesterday
/digest 2026-02-24
```

## What to do when this command is invoked

### Step 1: Load config

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/digest-cli.py" config
```

From the output, note:
- `obsidian.enabled` — determines format (callout vs blockquote)
- `obsidian.vault_path` / `output_dir` — where to write
- `obsidian.daily_notes_dir`, `obsidian.date_format`, `obsidian.folder_format` — daily note path
- `obsidian.section_heading` — heading to insert under
- `obsidian.wikilinks` — use `[[project]]` in headings
- `daily_format.project_heading` — template for project headings
- `min_turns` — minimum turns to include
- `language` — output language for summaries

### Step 2: List sessions

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/digest-cli.py" list --date today --min-turns {min_turns}
```

Use `--date yesterday` or `--date YYYY-MM-DD` if the user specified a different date.

If no sessions found, tell the user and stop.

### Step 3: Check daily note for existing entries

Determine daily note path:
- **Obsidian mode:** `{vault_path}/{daily_notes_dir}/{folder_format}/{date_format}.md`
- **Plain mode:** `{output_dir}/{YYYY-MM-DD}.md`

Read the daily note file. Search for `<!-- session:UUID -->` markers to identify which sessions are already written.

Separate sessions into:
- **New** — no marker found in daily note
- **Resumed** — marker exists (session was resumed, entry needs updating)

If all sessions are already written and none are resumed, tell the user "All sessions already in daily note" and stop.

### Step 4: Get session details

For each new or resumed session:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/digest-cli.py" show {session_id}
```

Read the `dialog` field to understand what happened in the session.

### Step 5: Generate summaries

For each session, generate a structured summary by analyzing the `dialog`:

1. **Category** — one of: feature, bugfix, refactor, research, config, docs, review, debug, testing, deploy, other
2. **Title** — 5-10 word description
3. **Body** — bullet points of what was done, key decisions, problems, TODOs

Write in the configured `language` (if set).

Be specific: mention file names, function names, concrete changes.

### Step 6: Write entries

Build entry blocks using the format from the `session-digest` skill:

**For Obsidian mode:**
```markdown
<!-- session:{id} -->
<!-- title:{title} -->
> [!bot]- **{start_time}** {category} · {duration}
> - {bullet points}
>
> `claude --resume {id}`
> *Branch: `{git_branch}` · Files: `{files}`*
```

**For plain mode:**
```markdown
<!-- session:{id} -->
<!-- title:{title} -->
**{start_time}** {category} · {duration}

> - {bullet points}
>
> `claude --resume {id}`
> *Branch: `{git_branch}` · Files: `{files}`*
```

Group entries under project headings (if `group_by_project: true`):
- Obsidian: `### 🤖 [[{project}]]`
- Plain: `### 🤖 {project}`

**For new sessions:** Append entries under the `section_heading` in the daily note.
**For resumed sessions:** Replace the existing entry block (from `<!-- session:ID -->` to the next session marker or heading).

Use Edit tool for updates, Write tool for new files.

### Step 7: Update summary

If `daily_summary: true`, update the `### Done` section with bullet list of all `<!-- title:... -->` values.

### Step 8: Report

Show the user what was written:
```
Wrote {N} session entries to {daily_note_path}

Sessions:
- {project}: {title} ({category}, {duration})
- ...
```
