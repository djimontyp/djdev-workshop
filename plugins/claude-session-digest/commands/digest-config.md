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

Run the CLI tool to get resolved configuration:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/digest-cli.py" config
```

Parse the JSON output and display it in a readable format:

```
claude-session-digest configuration
─────────────────────────────────────
Config file: {config_path}

Core settings:
  output_dir:  {output_dir}
  language:    {language}
  min_turns:   {min_turns}

Obsidian:
  enabled:        {obsidian.enabled}
```

When `obsidian.enabled=true`, also show all obsidian sub-keys:

```
Obsidian:
  enabled:         true  (Obsidian mode — writing to vault)
  vault_path:      {obsidian.vault_path}
  daily_notes_dir: {obsidian.daily_notes_dir}
  date_format:     {obsidian.date_format}
  folder_format:   {obsidian.folder_format}
  section_heading: {obsidian.section_heading}
  wikilinks:       {obsidian.wikilinks}
  template_path:   {obsidian.template_path}

Format:
  group_by_project: {daily_format.group_by_project}
  show_files:       {daily_format.show_files}
  show_branch:      {daily_format.show_branch}
  project_heading:  {daily_format.project_heading}
```

If `config_path` is null (no config found):

```
No config found.

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
Note: output_dir /path/to/dir does not exist yet — it will be created when you run /digest.
```
