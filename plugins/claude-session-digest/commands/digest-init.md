---
description: Interactive setup wizard for claude-session-digest
---

# /digest-init

Interactive setup wizard for claude-session-digest plugin.

## What to do when this command is invoked

### Step 1: Check existing config

Check if config already exists at any of these locations (first found):
1. `SESSION_DIGEST_CONFIG` env var
2. `{cwd}/.claude/session-digest.local.md`
3. `~/.claude/session-digest.local.md`

If config exists, show its path and ask: "Config already exists at {path}. Do you want to reconfigure?"
- If no — exit with current config summary
- If yes — continue to Step 2

### Step 2: Ask setup questions

Ask the user these questions (use AskUserQuestion or conversational approach):

**1. Output mode:**
- Plain markdown (simple files in a directory)
- Obsidian vault integration (daily notes with callouts, wikilinks)

**2. Output location:**
- If plain: ask for output directory (default: `~/daily-summaries`)
- If Obsidian: ask for vault path (e.g., `~/Documents/MyVault`)

**3. Language:**
- What language should summaries be written in? (default: null = English)
- Examples: `uk`, `French`, `Українська`, `日本語`

**4. AI model:**
- `sonnet` (default, best quality)
- `haiku` (faster, cheaper)
- `null` (offline — no API calls, uses first message as description)

### Step 3: Create config file

Create `~/.claude/session-digest.local.md` with the collected settings:

```markdown
---
output_dir: {output_dir}
model: {model}
language: {language}
min_turns: 3
obsidian_enabled: {true/false}
obsidian_vault_path: "{vault_path}"
obsidian_daily_notes_dir: Daily notes
obsidian_date_format: "%Y-%m-%d"
obsidian_folder_format: "%Y/%m"
obsidian_section_heading: "## Notes"
obsidian_wikilinks: true
group_by_project: true
show_files: true
show_branch: true
daily_summary: true
quiet: false
---
```

Only include obsidian_* keys if Obsidian mode was selected.

### Step 4: Verify

1. Read back the created config and display it (like /digest-config)
2. If output directory doesn't exist, mention it will be created on first session
3. Show a summary:

```
✅ session-digest configured!

Config: ~/.claude/session-digest.local.md
Mode: {Plain/Obsidian}
Output: {path}
Model: {model}
Language: {language or "English (default)"}

Next session end will create your first digest entry.
```

### Step 5: Suggest project-level config (optional)

If the user is in a project directory with `.claude/`:
"Want to create a project-specific override at `{cwd}/.claude/session-digest.local.md`? This would override the global config for this project only."
