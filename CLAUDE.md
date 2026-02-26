# Athena

Weekly planner (Tweek-inspired). Flask service on port 5002, deployed alongside duo-brain and Chronos on the Pi.

**Phil's Notes:** `/home/phil/Documents/philVault/Projects/Athena/`

## Architecture

- `app.py` — Flask routes + auth
- `config.py` — env-backed config (vault path, PIN hash, secret key)
- `daemons/auth.py` — PIN auth (shared pattern with Quanta/duo-brain)
- `daemons/weekplan.py` — all task CRUD, markdown parse/build
- `templates/index.html` — full week UI (SortableJS drag, Trello-style card panel)
- `templates/base.html` — base layout (JetBrains Mono, purple accent #b967ff, scanlines)
- `templates/login.html` — PIN keypad
- `static/css/style.css` — self-contained styles (purple theme, responsive)
- `static/icons/athena.svg` — pixel-art owl favicon

## Vault Storage

- Weekly files: `vault/Journals/Weekly/YYYY-WXX.md`
- Recurring tasks: `vault/Projects/Quanta/Templates/WeeklyRecurring.md`

### Markdown format
```
# Week 9 — Feb 23–Mar 1

## Monday
- [ ] Task text [recur:daily][attach:file.md]
  Optional note line here
- [x] Completed task

## Someday
- [ ] Backlog item
```

Notes stored as 2-space indented lines after the task. Carry forward on new week: recurring tasks + uncompleted Someday items.

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
| POST | `/api/week/attach` | Attach file |
| POST | `/api/week/reorder` | Reorder section |

## UI — Key Patterns

### Card Panel (Trello-style)
Clicking a task text opens a right-side panel (bottom sheet on mobile) with:
- Editable task title (saves on blur/Enter via `/api/week/rename`)
- Notes textarea (saves on blur or before any action)
- Recurring pills: None / Daily / Weekly / Weekdays / Biweekly / Monthly / Annually / Custom…
- Custom recur: inline `Every [N] [days/weeks/months/years/Sun…Sat]` form
- Defer to: Tomorrow / Next week / Someday
- Duplicate / Attach / Delete actions

`saveCardChanges()` is always awaited before any mutating action to prevent race conditions between note/title saves and API calls.

After `loadWeek` rebuilds the DOM, `renderWeek` re-opens the card panel for the same task via `savedCard` (matching on `section` + `section_idx`).

### Task Identification
Tasks are identified by `section_idx` (0-based position within their section), NOT file line numbers. Line numbers shift when notes are added/removed; section position is stable.

### Session Expiry
`fetchApi()` wraps `fetch()` and detects when a 302 redirects to `/login` (`res.redirected && res.url.includes('/login')`), then redirects the whole page.

### Drag-and-Drop
SortableJS with `handle: '.wp-drag-handle'` — drag only activates on the `⋮` grip, leaving clicks on task text free to open the card panel. Handle is hidden on mobile.

### Collapse Day Columns
Each day header has an eye button (`◉`). Click toggles `.collapsed` on the day column. Desktop: column shrinks to 26px with rotated day name (via `updateGridColumns()` which sets `grid-template-columns` dynamically). State persisted in `localStorage` key `athena-collapsed-days`.

### Enter → Next Column
After adding a task with Enter, `pendingFocusSection` is set to the next day. `renderWeek` focuses that section's add input after DOM rebuild.

## Deploy

```bash
bash deploy/deploy-to-pi.sh
```

Pi service: `/etc/systemd/system/athena.service`
Pi path: `/opt/athena/`
Logs: `/opt/athena/logs/`

## Key Gotchas

- SortableJS cross-column drag uses `group: 'week-tasks'` on all containers including Someday
- `closeCardPanel()` is async — awaits `saveCardChanges()` before clearing `activeCard`; otherwise the blur event fires after `activeCard` is null and the note/title save is skipped
- Someday container needs `data-date` and `data-section` set before SortableJS init
- `reorder_section` matches by task text — duplicate text names in the same section will collide
- Collapsed column width is set via JS (`updateGridColumns`), not CSS alone, because CSS grid `1fr` columns can't be individually sized without JS
