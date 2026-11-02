# Athena

Weekly planner — 7-column grid with Someday backlog. Flask service on port 5002, deployed alongside duo-brain and Chronos on the Pi.

**Phil's Notes:** `/home/phil/Documents/philVault/dev/Athena/`

## Vault Reorg (2026-08-25)

Phil restructured `philVault/` from ~14 sprawling top-level folders into 6 categories: `work/`, `dev/`, `creative/`, `life/`, `trading/`, `gaming/` — each with its own `projects/` and `journal/` subfolder. Athena's own notes moved from `Projects/Athena/` to `dev/Athena/`. Quanta (duo-brain) lives at `dev/Quanta/` now (was `Projects/Quanta/`).

`config.py`'s `TEMPLATES_DIR` was updated to `OBSIDIAN_VAULT / "dev" / "Quanta" / "Templates"` and deployed — fixed in both dev and prod. Note: the original `WeeklyRecurring.md` was found deleted (not just moved) during the reorg and was recreated as an empty placeholder at the new path.

Full migration plan/rationale: `philVault/dev/Quanta/vault-reorg-plan.md`.

## Deployment Rules
**Never deploy to the Pi without Phil explicitly saying so.** Do not run deploy scripts, rsync to `duobrain.local`, SSH to `pi@duobrain.local`, or restart services via `systemctl`. Always describe what you want to deploy and wait for confirmation. Enforced by `.claude/settings.json`'s `PreToolUse`/`Bash` hook (`guard_deploy_hook.sh`, shared from duo-brain), which forces a fresh confirmation on any deploy-shaped command regardless of what's pre-approved in `settings.local.json`.

⚠️ **Ecosystem governance applies here too** — this project is locked to its own root: no agent (including Quanta/Overlord) may edit files outside `~/Dev/Claude/athena/`, vault docs included. Full rules + enforcement status: `philVault/dev/Ecosystem-Governance.md`. Added 2026-09-13.

- DEV: `python3 app.py` → `http://localhost:5002`
- PROD: Pi at `duobrain.local` — deploy only on explicit instruction

## Key Resources
- **Architecture:** `philVault/dev/Quanta/Personal Hub Architecture – Step-by-Step Plan.md`

## Architecture

- `app.py` — Flask routes + auth, calls `init_db()` at startup
- `config.py` — env-backed config (vault path, PIN hash, secret key)
- `daemons/auth.py` — PIN auth (shared pattern with duo-brain)
- `daemons/weekplan.py` — all task CRUD against SQLite
- `daemons/blocks.py` — Time Blocks: global task-bucket containers, separate from the week grid
- `daemons/operations.py` — Operations: named, date-ranged focus modes that mute a day column down to Operation work
- `migrate.py` — one-time import of legacy markdown week files into DB
- `templates/index.html` — full week UI (SortableJS drag, card panel)
- `templates/base.html` — base layout (JetBrains Mono, purple accent #b967ff, scanlines)
- `templates/login.html` — PIN keypad
- `static/css/style.css` — self-contained styles (purple theme, responsive)
- `static/icons/athena.svg` — pixel-art owl favicon

## Storage

**Database:** `data/athena.db` (SQLite, WAL mode)

```sql
tasks (
    id           TEXT PRIMARY KEY,   -- UUID4
    week_monday  TEXT NOT NULL,       -- ISO date of the week's Monday
    section      TEXT NOT NULL,       -- 'Monday'…'Sunday' or 'Someday'
    position     INTEGER NOT NULL,    -- 0-based order within section
    text         TEXT NOT NULL,
    checked      INTEGER DEFAULT 0,
    recur        TEXT,                -- 'daily', 'every 2 weeks', 'every Mon', etc.
    attachment   TEXT,
    note         TEXT,
    created_at   TEXT NOT NULL,
    completed_at TEXT,                -- set when checked, cleared when unchecked
    source       TEXT                 -- 'csv_import' for Google Calendar CSV imports, NULL otherwise
)
```

**Global recurring tasks:** `vault/dev/Quanta/Templates/WeeklyRecurring.md`
Read-only markdown list, shown in every day column as ghost indicators.

**Time Blocks** (`daemons/blocks.py`) — global, week-independent task buckets:

```sql
time_blocks (id, name, position, created_at)

time_block_tasks (
    id, block_id, position, text, checked, note, color,
    created_at, completed_at
)
```

**Operations** (`daemons/operations.py`) — named focus modes pinned to a date range:

```sql
operations (id, name, start_date, end_date, created_at)
```

`tasks.operation_id` tags a day task into an Operation. Like `tasks.block_id` it has
no FK/cascade — `delete_operation()` clears it manually, so the tasks survive and
simply stop being Operation work. Only dated day tasks can be tagged: Scratchpad and
Someday have no calendar date, so no Operation can ever cover them.

An Operation also owns a flat **Mission Objectives** checklist — date-independent,
unlike everything else tagged/linked elsewhere in this app:

```sql
operation_tasks (id, operation_id, position, text, checked, created_at, completed_at)
```

Unlike `tasks.operation_id`, this one *is* a real FK with `ON DELETE CASCADE`
(mirroring `time_block_tasks -> time_blocks`) — an objective has no meaning once
its Operation is gone, so `delete_operation()` doesn't need to clean it up by hand.

A block holds two kinds of tasks: "own" tasks (live only in `time_block_tasks`, no day
of their own) and "linked" tasks (a real `tasks` row tagged via `tasks.block_id` — stays
on its day, just also surfaces inside the block). `tasks.block_id` has no FK/cascade;
`delete_block()` clears it manually.

## API Routes

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/week?date=YYYY-MM-DD` | Full week data |
| POST | `/api/week/task` | Add task |
| DELETE | `/api/week/task` | Delete task |
| POST | `/api/week/toggle` | Check/uncheck |
| POST | `/api/week/note` | Set/clear note |
| POST | `/api/week/recur` | Set recurrence |
| POST | `/api/week/rename` | Rename task text |
| POST | `/api/week/defer` | Defer to tomorrow/next_week/someday |
| POST | `/api/week/duplicate` | Duplicate task |
| POST | `/api/week/attach` | Attach file reference |
| POST | `/api/week/reorder` | Reorder section |
| POST | `/api/week/scratchpad-purge` | Move checked Scratchpad tasks to today's day column (stays checked) |
| POST | `/api/import/calendar-csv` | Import Google Calendar-compliant CSV (multipart `file` or `csv_text`) |
| GET | `/api/imported` | List CSV-imported tasks (flat, sorted by date) |
| DELETE | `/api/imported` | Delete one CSV-imported task by `id` |
| GET | `/api/blocks` | List all blocks with their own + linked tasks |
| POST | `/api/blocks` | Add block |
| DELETE | `/api/blocks` | Delete block (clears `block_id` on any linked tasks) |
| POST | `/api/blocks/rename` | Rename block |
| POST | `/api/blocks/task` | Add "own" task to a block |
| POST | `/api/blocks/task/toggle` | Check/uncheck block task |
| POST | `/api/blocks/task/rename` | Rename block task |
| POST | `/api/blocks/task/note` | Set/clear block task note |
| POST | `/api/blocks/task/color` | Set block task color |
| DELETE | `/api/blocks/task` | Delete "own" block task |
| POST | `/api/blocks/task/reorder` | Reorder tasks within a block |
| POST | `/api/blocks/task/move` | Move "own" task between blocks |
| POST | `/api/blocks/task/link` | Tag a day/Scratchpad/Someday task into a block |
| POST | `/api/blocks/task/unlink` | Remove a linked task from its block (task itself untouched) |
| POST | `/api/blocks/task/send-to-day` | Turn an "own" block task into a real day task, still linked |
| POST | `/api/blocks/reset-stale` | Sweep leftovers from a previous day — see below |
| POST | `/api/week/operation` | Tag/untag a day task into an Operation (`operation_id: null` clears) |
| GET | `/api/operations` | List all Operations with day/task counts and status |
| POST | `/api/operations` | Launch an Operation (`name`, `start_date`, `end_date`) |
| POST | `/api/operations/rename` | Rename an Operation |
| POST | `/api/operations/dates` | Re-range an Operation (untags days it no longer covers) |
| DELETE | `/api/operations` | Delete an Operation (clears `operation_id` on its tasks) |
| POST | `/api/operations/objective` | Add a mission objective to an Operation |
| POST | `/api/operations/objective/toggle` | Check/uncheck a mission objective |
| POST | `/api/operations/objective/rename` | Rename a mission objective |
| DELETE | `/api/operations/objective` | Delete a mission objective |
| POST | `/api/operations/objective/reorder` | Reorder an Operation's objectives |

## UI — Key Patterns

### Card Panel
Clicking a task opens a right-side panel (bottom sheet on mobile):
- Editable title (saves on blur/Enter via `/api/week/rename`)
- Notes textarea (saves on blur or before any mutating action)
- Recurrence pills + custom `Every [N] [unit]` inline form
- Defer / Duplicate / Attach / Delete

`saveCardChanges()` (note + title) is always awaited before any API mutation. `closeCardPanel()` is async for the same reason — it saves before clearing `activeCard`, otherwise the blur fires after the card is null and the save is skipped.

After `loadWeek` rebuilds the DOM, `renderWeek` re-opens the card for the same task via `savedCard` (matched on `section` + `section_idx`).

### Task Identification
Tasks are referenced by `section_idx` (0-based position in section) in the API. The DB uses UUIDs internally; `_get_task_by_idx` maps index → UUID by ordering on `position`.

### Session Expiry
`fetchApi()` wraps `fetch()` and detects 302 redirects to `/login`, then redirects the whole page. Without this, expired sessions fail silently — `fetch()` follows the redirect, gets HTML, and `res.json()` throws.

### Drag-and-Drop
SortableJS with `handle: '.wp-drag-handle'` — drag only activates on the grip element. Handle is hidden on mobile (touch users can't drag anyway).

### Collapse Day Columns
Eye button (`◉`) in each day header toggles `.collapsed`. Desktop: `updateGridColumns()` sets `grid-template-columns` dynamically (CSS grid `1fr` can't be individually overridden without JS). State persisted in `localStorage['athena-collapsed-days']`.

### Enter → Next Column
`pendingFocusSection` is set on Enter keydown. After `renderWeek` rebuilds the DOM, it focuses that section's add input.

### Time Blocks — Leftover Reset
Opening Block view detects tasks left over from a previous day — "own" tasks by
`created_at` date, "linked" tasks by their derived `date_str` — and prompts once per
calendar day (`localStorage['athena-blocks-reset-dismissed']` holds the dismissed date)
to reset. Confirming calls `reset_stale_tasks()` (blocks.py): unchecked leftovers are
filed onto **yesterday's** day column (own tasks via `add_task`, linked tasks re-dated
and unlinked); checked leftovers are discarded — own tasks deleted outright, linked
tasks just unlinked in place since they're real, already-correctly-dated history.

### Operations
An Operation is a name plus a contiguous, inclusive date range. Launch/manage them
from the `◎` header button (`#operations-modal`, `z-index: 2001`).

While an Operation covers a day column, that column mutes the global
`WeeklyRecurring` ghosts and gets an accent border + a `NAME 2/4` day-count bar
(`.day-op-bar`). **It does not hide anything else.** An earlier version hid every
untagged task too ("tunnel vision") — reverted 2026-11-01 after real use showed a
multi-day Operation was wiping out someone's whole regular action-item list for
the entire span, not just muting low-value noise. A task tagged into the
Operation (`tasks.operation_id`) is marked rather than isolated: a ◆ badge in
`makeTaskEl`'s badge row plus a `.op-tagged` left-border accent on the row. If
tunnel vision (or something like it) comes back, it needs to default to
*showing* everything and require an opt-in to narrow, not the other way round.

Supporting pieces:
- Tasks typed into an Operation day's `+` input, or dragged into one, auto-tag
  into the Operation (`operationIdForDate`); dragging out drops the tag. This
  survived the tunnel-vision revert on purpose — harmless now that tagging is
  purely cosmetic (badge + `task_count`), and it's the kind of default someone
  running a multi-day Operation would likely want. Deliberately *not* matched
  to how Time Blocks works (no auto-link there) — revisit if that inconsistency
  becomes confusing in practice.
- `defer_task` and `set_operation_dates` clear `operation_id` when the task's new
  date falls outside the range — a tag only means anything on a covered date.
- Card panel shows an Operation pill row, but only for Operations that actually
  cover that task's day. `makeTaskEl`'s click handler must pass `operation_id`
  through to `openCardPanel` explicitly (it's not derived from anything else on
  the element) — this was missing for one revision, silently making every card
  panel show "Not in Operation" regardless of actual tag state.

### Operation Marquee
A scrolling alert band (`#wp-op-marquee`) sits between the page header and the week
nav whenever an Operation overlaps the week on screen, reading
`◆ OPERATION: QUANTA ◆ DAY 2 OF 4 ◆`.

`renderOpMarquee` repeats the segment until one half overflows the viewport, then
duplicates it so the `translateX(-50%)` keyframe loops seamlessly; duration is set
from the measured half-width so speed stays constant regardless of name length.
It re-measures on `resize`, and `prefers-reduced-motion` keeps the band but drops
the scroll.

### Mission Objectives
Each Operation owns a flat checklist (`operation_tasks`) with no date, no section,
no day column — it exists from launch to deletion regardless of which week is on
screen, unlike a tagged day task (date-bound) or Scratchpad (week-bound). Two
surfaces share the same markup/listener code (`objectivesListHtml()` +
`wireObjectiveListeners()`):

- **Week view** — `#wp-op-objectives`, a collapsible "Mission Objectives — &lt;name&gt;"
  group per Operation covering the week on screen, rendered by
  `renderOpObjectivesPanel()` right after `renderOpMarquee()` in `renderWeek`.
  Collapse state is per-Operation in `localStorage['athena-op-objectives-collapsed']`.
- **Operations modal** — an expand toggle (`▸`) per row reveals the same checklist
  inline, reachable regardless of which week is in view (e.g. to stock objectives
  before the Operation's range even starts).

Both call `refreshOperationsEverywhere()` after any add/toggle/rename/delete, which
re-fetches `/api/operations` and re-renders whichever of the two surfaces is
currently open — a full rebuild, not a DOM patch, matching how blocks/recurring/
birthdays already refresh themselves elsewhere in this file.

## Carry-Forward Logic

When a new week is first accessed, `get_or_create_week` queries the previous week's Monday and copies:
- All unchecked tasks with a `recur` value (any section)
- All unchecked Someday tasks

Positions are renormalized after copy.

## Deploy

```bash
bash deploy/deploy-to-pi.sh
```

Pi service: `/etc/systemd/system/athena.service`
Pi app path: `/opt/athena/app/`
Pi venv: `/opt/athena/venv/`
Logs: `/opt/athena/logs/`

## Key Gotchas

- `reorder_section` matches tasks by text (first match wins) — duplicate names in the same section will collide
- SortableJS cross-column drag uses `group: 'week-tasks'` on all containers including Someday
- Someday container needs `data-date` and `data-section` set before SortableJS init
- `migrate.py` must be run from `/opt/athena/app/` on the Pi so `config` is importable
- `import_calendar_csv`: `csv.DictReader` gives short rows `None` (not `""`) for missing trailing columns, and unquoted commas inside the last column spill into `row[None]` (restkey) instead of staying in that field — both handled defensively, but a reminder that hand-built "Google Calendar CSV" exports are rarely well-formed
- Operations no longer hide real tasks (see "Operations" above) — only the `WeeklyRecurring` ghosts mute on a covered day. If a task looks genuinely missing, that's a data bug, not Operation filtering
- Two Operations may overlap; the first by `start_date, created_at` wins a contested day (`op_id_by_date.setdefault`)
- Any new full-screen modal shown *while `#blocks-modal` is open* needs an explicit `z-index` above 2000 — `#blocks-modal` and the generic `.wp-modal-overlay` class both sit at `z-index: 2000`, and at a tie `#blocks-modal` (later in the DOM) paints over the other modal even though `style.display` is correctly `flex`. `#wp-blocks-reset-modal` sets `z-index: 2001` for this reason.
