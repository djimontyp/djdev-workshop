# /digest-config

Show and validate the current claude-session-digest configuration.

## Usage

```
/digest-config
/digest-config show
```

## What to do when this command is invoked

Read the configuration file at `~/.config/session-digest/config.json` (or the path in `SESSION_DIGEST_CONFIG` env var if set).

Display the configuration in a readable format:

```
claude-session-digest configuration
─────────────────────────────────────
Config file: ~/.config/session-digest/config.json

Core settings:
  output_dir:  ~/Documents/daily-summaries
  model:       haiku  (AI summaries enabled)
  language:    uk  (detected from Claude settings: "Українська")
  min_turns:   3

Obsidian:
  enabled:     false  (plain mode — writing to output_dir)

Format:
  group_by_project: true
  show_tools:       true
  show_files:       false
  show_branch:      true
```

If the config file does not exist at `~/.config/session-digest/config.json`:

```
⚠️  No config found at ~/.config/session-digest/config.json

To get started:
1. Copy the example config:
   cp "$(claude plugin path claude-session-digest)/config.example.json" ~/.config/session-digest/config.json

2. Edit it and set your output_dir:
   open ~/.config/session-digest/config.json

3. Run /digest-config again to verify.
```

If the file exists but has JSON parse errors, show the error with line number and a hint to validate with `python3 -m json.tool ~/.config/session-digest/config.json`.

After showing the config, if `output_dir` is set, check whether that directory exists. If not, mention it:

```
Note: output_dir /path/to/dir does not exist yet — it will be created on first session.
```
