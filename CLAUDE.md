# Athena

Weekly planner — 7-column grid with Someday backlog. Flask service on port 5002, deployed alongside duo-brain and Chronos on the Pi.

**Phil's Notes:** `/home/phil/Documents/philVault/Projects/Athena/`

## Key Resources
- **Architecture:** `philVault/Projects/Quanta/Personal Hub Architecture – Step-by-Step Plan.md`

## Architecture

- `app.py` — Flask routes + auth, calls `init_db()` at startup
- `config.py` — env-backed config (vault path, PIN hash, secret key)
- `daemons/auth.py` — PIN auth (shared pattern with duo-brain)
- `daemons/weekplan.py` — all task CRUD against SQLite
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
    completed_at TEXT                 -- set when checked, cleared when unchecked
)
```

**Global recurring tasks:** `vault/Projects/Quanta/Templates/WeeklyRecurring.md`
Read-only markdown list, shown in every day column as ghost indicators.

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
