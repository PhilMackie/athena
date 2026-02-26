# Athena

A weekly planner with a 7-column task grid and Someday backlog. Built with Flask, deployed on a Raspberry Pi alongside duo-brain and Chronos.

## Features

- **7-day grid** with drag-and-drop reordering between columns (SortableJS)
- **Someday backlog** — uncompleted Someday tasks carry forward each week
- **Card panel** — click any task to open a side panel with all actions
  - Editable task name
  - Notes textarea
  - Recurrence picker (Daily, Weekly, Weekdays, Biweekly, Monthly, Annually, or custom `Every N days/weeks/months/years/Sun–Sat`)
  - Defer to Tomorrow / Next week / Someday
  - Duplicate, Attach file, Delete
- **Collapse day columns** — hide past days to a thin strip (desktop), persisted in localStorage
- **Global recurring tasks** — loaded from `WeeklyRecurring.md`, shown in every column as read-only
- **PIN authentication** with lockout after failed attempts
- **SQLite storage** — tasks stored in `data/athena.db` with UUIDs and timestamps
- **Responsive** — flat list layout on mobile, larger text, checkboxes on the right

## Stack

- Python / Flask + Gunicorn
- SQLite (WAL mode)
- Vanilla JS (no framework)
- SortableJS for drag-and-drop
- JetBrains Mono, purple `#b967ff` accent, scanlines aesthetic

## Setup

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # set VAULT_PATH, PIN_HASH, SECRET_KEY
python app.py
```

Runs on `http://localhost:5002`. The database is created automatically on first run.

## Migrating from markdown

If you have existing week files in `vault/Journals/Weekly/`:

```bash
python migrate.py
```

Run once. Safe to re-run — skips if the database already has data.

## Deploy to Pi

```bash
bash deploy/deploy-to-pi.sh
```

Service: `athena.service` (systemd), Pi path: `/opt/athena/`

## Architecture

See `CLAUDE.md` for code architecture, key patterns, and gotchas.
