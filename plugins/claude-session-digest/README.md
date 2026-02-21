# claude-session-digest

> Auto-summarize Claude Code sessions into daily markdown digests.

Automatically captures what you worked on, summarizes it with AI (Haiku), and writes structured daily notes — either as plain markdown files or into your Obsidian vault.

## ⚠️ API Usage Warning

**This plugin makes an API call after EVERY Claude Code session.**

When a session ends, the script calls `claude -p --model haiku` to generate a summary. This means:

- **Each session = 1 additional API request** to Haiku
- On Pro/Max subscription, Haiku has a separate quota — usually free
- On API billing these are **real costs** per call
- Dozens of sessions per day = dozens of extra requests

**To disable AI summarization** (offline mode, no API calls):

```json
{ "model": null }
```

This uses the first user message as the session description instead. Works completely offline.

---

## Quick Start

### 1. Install the plugin

```bash
claude plugin install djimontyp/djdev-workshop/claude-session-digest
```

### 2. Create your config

```bash
mkdir -p ~/.config/session-digest
cp "$(claude plugin path claude-session-digest)/config.example.json" ~/.config/session-digest/config.json
```

Edit `~/.config/session-digest/config.json` and set your `output_dir`:

```json
{
  "output_dir": "~/Documents/daily-summaries",
  "model": "haiku",
  "min_turns": 3
}
```

### 3. That's it

Next time you end a Claude Code session, a summary entry appears in your daily file.

---

## Configuration

Config file: `~/.config/session-digest/config.json`

**This file is never overwritten by plugin updates.**

### Core Options

| Key | Default | Description |
|-----|---------|-------------|
| `output_dir` | required | Directory for daily `.md` files |
| `language` | `null` | Summary language. `null` = auto from Claude settings |
| `model` | `"haiku"` | AI model for summaries. `null` = offline mode |
| `min_turns` | `3` | Skip sessions shorter than N user messages |

### Obsidian Integration

```json
{
  "obsidian": {
    "enabled": true,
    "vault_path": "/Users/you/Documents/MyVault",
    "daily_notes_dir": "Daily notes",
    "date_format": "%Y-%m-%d",
    "folder_format": "%Y/%m",
    "section_heading": "## Notes",
    "wikilinks": true,
    "template_path": ""
  }
}
```

When `enabled: true`, sessions are written into your Obsidian vault daily notes instead of plain files. The plugin inserts entries under `section_heading`, grouped by project.

### Format Options

```json
{
  "daily_format": {
    "group_by_project": true,
    "show_tools": true,
    "show_files": false,
    "show_branch": true,
    "project_heading": "### 🤖 {project}",
    "entry_format": "**{time}** · `{category}` · {duration}"
  }
}
```

---

## Output Format

### Plain Mode

```markdown
# Session Digest — 2026-02-21

### 🤖 my-project

<!-- session:abc123 -->
**09:15** · `feature` · 45m
> Implemented Telegram message parsing. Added extraction pipeline.

<!-- session:xyz789 -->
**20:00** · `refactor` · 1h 10m
> Refactored LoginPresenter. Updated CSS tokens.
```

### Obsidian Mode (with wikilinks)

```markdown
### 🤖 [[my-project]]

<!-- session:abc123 -->
**09:15** · `feature` · 45m
> Implemented Telegram message parsing.
```

---

## Commands

### `/digest-config`

Shows your current configuration:

```
Current config: ~/.config/session-digest/config.json
  output_dir: ~/Documents/daily-summaries
  model: haiku
  language: uk (detected from Claude settings)
  min_turns: 3
  obsidian: disabled
```

---

## Daily Assistant Agent

This plugin includes a `daily-assistant` agent that knows about your Claude sessions and daily notes:

- **Morning** — shows yesterday's sessions, reminds of unfinished work
- **Evening** — includes sessions in day review
- **Analysis** — aggregates sessions by project, shows statistics
- **Notes** — quick notes with context from your vault

The agent reads from `~/.config/session-digest/config.json` — no hardcoded paths.

---

## Troubleshooting

**No entries appearing?**

1. Check config exists: `cat ~/.config/session-digest/config.json`
2. Check output dir exists: `ls ~/daily-summaries` (or your configured path)
3. Was the session long enough? Check `min_turns` setting
4. Run manually to test:

```bash
TRANSCRIPT=$(ls ~/.claude/projects/*/*.jsonl 2>/dev/null | tail -1)
echo "{\"session_id\":\"test\",\"transcript_path\":\"$TRANSCRIPT\",\"cwd\":\"$(pwd)\",\"reason\":\"user_exit\",\"hook_event_name\":\"SessionEnd\"}" | \
  python3 "$(claude plugin path claude-session-digest)/scripts/session-digest.py"
```

**AI summaries not working?**

Set `"model": null` to use offline mode (first user message as description).

---

## License

Apache 2.0 — free to use and fork, attribution required.
