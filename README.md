# Timer Tracker

Use with clockify, (fine vs coarse-grain time record)

```
Usage: time-tracker [OPTIONS] COMMAND [ARGS]...

Options:
  --version  Show the version and exit.
  --help     Show this message and exit.

Commands:
  end     Ends the timer and record entry
  log     Show all entries
  show    Show entries for a given date
  start   Starts the timer
  status  Checks the timer status
  switch  Switches the timer and record entry
  sync    Upload unsynced entries to Clockify
```

## Clockify sync

Run `tt sync` to upload completed entries whose `synced` flag is false or
missing. Each successful upload is immediately saved as `"synced": true` in
`log.jsonl`. Failed uploads stay unsynced; the command exits with a nonzero status.
`tt sync --dry-run` previews payloads without uploading or changing the log.

The API key is resolved in this order:

1. `CLOCKIFY_API_KEY` environment variable.
2. `%LOCALAPPDATA%/tt/clockify-api.txt` (trimmed plain text).
3. A hidden Click prompt; the entered key is saved to that file.

Keep that file private: it contains the API key in plain text. Outside Windows,
the default data directory is `~/.local/share/tt/` (or `$XDG_DATA_HOME/tt/`).
The workspace ID follows the same order: `CLOCKIFY_WORKSPACE_ID`, then
`%LOCALAPPDATA%/tt/clockify-workspace.txt`, then a Click prompt that saves the ID to
that file. Both files use the same data directory as the log, including any
`TT_DATA_DIR` override.
There is no automatic default-workspace selection.

Projects use `clockify_id` in `projects.toml`; missing or archived
mappings upload without a project and produce a warning. As with the original
script, local timestamps are converted using the machine's local timezone.

Sync is upload-only: it does not update or delete existing Clockify entries.
Editing a synced local entry does not reset its flag. Existing uploads need their
local flags marked true before the first sync to avoid duplicates.

If Clockify accepts an upload but the response or local save is lost, an entry
may remain unsynced even though it exists remotely. Inspect Clockify before
retrying uncertain uploads. A lock prevents simultaneous `tt sync` runs; if a
process crashes, remove `<TT_DATA_DIR>/.clockify-sync.lock` after checking that
no sync is running. Avoid editing the log while syncing.

`scripts/clockify.py` now delegates to the same command; `--from` is no longer used.
