---
description: Show and validate the current claude-session-digest configuration
---

# /digest-config

Show and validate the current claude-session-digest configuration.

## Usage

```
/digest-config
/digest-config show
```

## What to do when this command is invoked

Resolve the config file path using the following cascade (first found wins):

1. `SESSION_DIGEST_CONFIG` env var — if set, use that path
2. `{cwd}/.claude/session-digest.local.md` — per-project override
3. `~/.claude/session-digest.local.md` — user-level defaults

Display the resolved path and configuration in a readable format:

```
claude-session-digest configuration
─────────────────────────────────────
Config file: ~/.claude/session-digest.local.md

Core settings:
  output_dir:  ~/Documents/daily-summaries
  model:       sonnet  (AI summaries enabled)
  language:    null
  min_turns:   3
  quiet:       false

Obsidian:
  enabled:        false  (plain mode — writing to output_dir)
```

When `obsidian_enabled=true`, also show all sub-keys:

```
Obsidian:
  enabled:         true  (Obsidian mode — writing to vault)
  vault_path:      ~/Documents/Obsidian
  daily_notes_dir: Daily notes
  date_format:     YYYY-MM-DD
  folder_format:   YYYY/MM
  section_heading: ## Notes
  wikilinks:       true
  template_path:   null

Format:
  group_by_project: true
  show_files:       true
  show_branch:      true
  project_heading:  "### 🤖 {project}"
```

If no config file is found at any location:

```
⚠️  No config found.

To get started:
1. Copy the example config:
   cp "$(claude plugin path claude-session-digest)/config.example.md" ~/.claude/session-digest.local.md

2. Edit it and set your output_dir:
   open ~/.claude/session-digest.local.md

3. Run /digest-config again to verify.

Per-project override (optional):
   cp "$(claude plugin path claude-session-digest)/config.example.md" .claude/session-digest.local.md
```

If the file exists but has parse errors (missing frontmatter or malformed YAML), show the error and hint to check the `---` markers are present.

After showing the config, if `output_dir` is set, check whether that directory exists. If not, mention it:

```
Note: output_dir /path/to/dir does not exist yet — it will be created on first session.
```
