# `tt` Refactor

## Goal

Evolve the current timer into a fine-grained project time tracker while preserving its
quick, low-friction CLI. Support explicit project selection and a future Jev-assisted
description-to-project workflow. Keep this document as a proposal; no implementation
changes are included.

## Product principles

- Starting, checking, stopping, and switching timers should remain fast and predictable.
- A completed entry records a start time, elapsed duration, description, and project.
- Projects are user configuration, not a catalogue that must be hosted or synced.
- Project assignment by Jev is optional and must be reviewable; never silently
  misattribute time.
- Core tracking works offline. Clockify integration is an optional adapter.
- Data format and CLI behaviour should be documented and migratable.

## Data model

### Project

- `id: str` - stable unique identifier
- `name: str` — required
- `clockify_id: str | None` — Clockify project identifier if it exists
- `description: str` —  human-readable description;
- `github_url: str | None` — repository URL if it exists.

### Entry

- `id: str` — stable unique identifier, useful for future editing and syncing.
- `start_time: datetime` — timezone-aware ISO 8601 timestamp.
- `duration_seconds: int` — non-negative elapsed time captured when stopped.
- `description: str` — user-provided text; empty descriptions should be allowed only if
  deliberately chosen.
- `project_id: str | None` — internal project key; `None` represents unassigned/Unknown.

Store durations as integer seconds, not formatted `HH:MM:SS`, so entries longer than 24
hours remain correct. Preserve timezone offsets in timestamps and convert explicitly at
API boundaries.

## Storage

### Canonical locations

Use the platform's standard per-user application-data directories, not the installed
package directory or current working directory:

- Windows: `%APPDATA%\\tt\\`
- Linux/Unix: `$XDG_CONFIG_HOME/tt/` for configuration (default
  `~/.config/tt/`) and `$XDG_DATA_HOME/tt/` for mutable records (default `~/.local/share/tt/`).


- Configuration: `projects.toml` in the user config directory.
- Records: `log.jsonl` in the user data directory.
- Active timer: `.alive` in the user data directory.
- Optional logs/cache: a separate cache directory; never make it authoritative.

Allow explicit overrides such as `TT_CONFIG_DIR` and `TT_DATA_DIR`. Resolve and
display the effective paths with a diagnostic command. Do not store user data in
the source tree, virtual environment, or package installation location.

### Project configuration: TOML

Use TOML for `projects.toml`; Python 3.11+ includes `tomllib`, while the declared
minimum may be lower, so either set the supported Python minimum to 3.11 or use a TOML
reader for older supported versions. TOML is appropriate for hand-edited project
metadata, but it is not the entry store.

Example:

```toml
[[projects]]
id = "f22d2a"
name = "Laserpath"
clockify_id = "abc123"
description = "CAD software for Aligned Vision to aid in part file generation"
github_url = "https://github.com/example/laserpath"
```

Provide `tt check` to validate config and report errors. editing of the
toml file should be done manually. 

### Entry storage: JSON Lines initially

Prefer append-only JSON Lines (`log.jsonl`) for the first version. Each line is one
versioned JSON object. It is easy to inspect, back up, export, and append without
introducing a database dependency. Use atomic writes for active timer state and validate
malformed lines with useful line-number diagnostics. Define a backup/export format and
keep a schema version on records.

SQLite is a reasonable later migration if edits, large datasets, robust transactions, or
complex reports make JSONL awkward. Keep a storage interface so CLI and integrations do
not depend directly on JSONL. Avoid choosing a database pre-emptively; do not maintain
two authoritative stores.

### Active timer and crash behaviour

`.alive` should contain a start timestamp. Starting must fail clearly if a timer
is already active. Stop/switch should append a completed entry before removing
active state; use atomic replacement for state updates. Document recovery for
interrupted writes and offer a status/recovery path rather than silently
discarding a timer.

## CLI API

Keep `tt` as the executable and use Click (already a dependency). Commands should have
consistent help, output, exit codes, and non-interactive options. Preserve familiar
commands where practical.

### Timer commands

`tt [COMMAND] [ARGS] [OPTIONS]`
`tt --help`
`tt --version`

- `tt start` - no args
- `tt switch [DESCRIPTION]` - optional description, editor if not provided
- `tt end [DESCRIPTION]` - optional description, editor if not provided
- `tt status` - show active timer info
- `tt show [-d DATE]` - show entries for a day, default today
- `tt log [--json]` - show all entires (filtering to be down with jq or other
  cli tools)
- `tt check` - validate config and data files, report errors

- `tt `

## Jev-assisted project assignment

Treat Jev as a pluggable, optional suggestion provider rather than a required dependency
in the core CLI. Define an interface that accepts the description and configured project
metadata and returns ranked project suggestions with confidence/explanation. Provide a
human confirmation step or explicit opt-in auto-assignment threshold. Preserve the
original description and record whether assignment was user-selected or suggested if
auditability is useful.

Initial workflow options:

1. Prompt for a project when stopping an unassigned timer; Jev may offer a suggestion.
2. A future non-CLI integration can submit descriptions through the same
   application/service layer.
3. If Jev is unavailable, errors, or has low confidence, save as unassigned and allow
   `tt entry assign` to correct it later.

Keep model credentials out of project TOML and source control; use environment variables
or the provider's supported credential mechanism.

## Clockify compatibility

- Match local projects to Clockify via optional `clockify_id`.
- Upload only entries with a mapped project when the Clockify API requires it; clearly
  report skipped/unmapped entries.
- Track sync state/remote entry IDs or use a stable idempotency strategy so repeated
  syncs do not create duplicates.
- Make date filtering inclusive and explicit, including timezone conversion.
- Preserve local records if API requests fail; show per-entry errors and support retry.
- Keep Clockify-specific API code outside the core storage/model layer.

## Packaging and project structure

Make the installed package the supported entry point, with `tt` registered in
`pyproject.toml`. Move implementation toward a package, for example:

```text
src/
  tt.py
scripts/
  ...clockify stuff, etc
.env
README.md
pyproject.toml
Makefile
```

Keep scripts thin wrappers or convert them to subcommands. Add dependencies deliberately
(`platformdirs`; TOML reader only if supporting Python before 3.11). Declare and test a
realistic Python minimum, build a wheel, and verify installation in a clean environment.
The current project declares Python `>=3.8`, which conflicts with PEP 604 union syntax
already used in the code; settle the minimum explicitly as part of the refactor.

## Migration from current data

Provide a one-time `tt migrate` (or automatic, backed-up migration) that reads the
existing `data/log.txt` and `_current_stamp` format. Create an `Unknown`/unassigned
representation for historical records, since they have no project. Preserve timestamps
and descriptions, and fix duration parsing to retain whole days where possible. Do not
modify or delete the original files; write migrated data to the canonical data directory
and report counts/errors. If an old timer is active, carry its timestamp forward as
active state after validation.

## Reliability, privacy, and testing

- Never overwrite logs/config silently; create backups before migrations or destructive
  config changes.
- Use atomic replacement for TOML and active state writes; serialize JSONL appends
  safely and document single-user/single-process assumptions.
- Validate input and produce actionable errors for corrupt config, records, missing
  environment variables, and unavailable editors.
- Ensure descriptions and local records are not sent to Jev or Clockify without an
  explicit user-configured integration action.
- Test duration boundaries (>24 hours), timezone and date filtering, switch failure
  ordering, editor cancellation, malformed files, duplicate project names/IDs,
  migration, and repeated Clockify sync.
- Add CLI tests using temporary config/data directories; avoid touching real user data
  or making network calls in tests.

## Suggested implementation sequence

1. Confirm schema, project ID strategy, supported Python version, and path overrides.
2. Add models, path resolution, TOML validation, JSONL storage, and active-state
   handling.
3. Implement migration and test it against a copy of existing data.
4. Implement timer and project commands while preserving familiar workflows.
5. Add export/reporting and colour-aware rendering.
6. Extract Clockify as an optional, idempotent integration.
7. Define and then implement the Jev suggestion adapter with confirmation behaviour.
8. Build and install the package in a clean environment; update README with commands,
   paths, backup, and migration instructions.
