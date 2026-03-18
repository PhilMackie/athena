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
  - Deleting a recurring task prompts: **This one** or **All future**
  - NRA / DWM binding — completing the task fires the linked duo-brain item
- **Collapse day columns** — hide past days to a thin strip (desktop), persisted in localStorage
- **Hide done** — toggle button (desktop) or shake (mobile) to hide completed tasks
- **Enter → next column** — hitting Enter in an add input moves focus to the next day's input
- **Global recurring tasks** — loaded from `WeeklyRecurring.md`, shown in every column as read-only
- **PIN authentication** with lockout after failed attempts, 30-day session
- **SQLite storage** — tasks stored in `data/athena.db` with UUIDs and timestamps
- **Responsive** — flat list layout on mobile

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

## Cross-App SSO

Athena participates in the shared SSO network with Quanta (`:5000`) and Chronos (`:5001`). Two auth paths are accepted automatically before every request:

- **URL token** — Quanta appends a signed 5-minute token when linking here. Athena verifies it, logs you in, and strips the token from the URL.
- **Network cookie** — Any successful login (PIN or token) sets a signed `network_auth` cookie valid for 30 days. Athena accepts this cookie from any port, so navigating here directly also skips the PIN.

**Requirement:** `SECRET_KEY` in `.env` must match across all three apps. See Quanta's README for the full protocol and how to add new apps to the network.

## Architecture

See `CLAUDE.md` for code architecture, key patterns, and gotchas.
