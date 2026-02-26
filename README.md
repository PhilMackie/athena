# Athena

A Tweek-inspired weekly planner with a 7-column task grid and Someday backlog. Stores tasks as Obsidian-compatible markdown in the vault. Built with Flask, deployed on a Raspberry Pi alongside duo-brain and Chronos.

## Features

- **7-day grid** with drag-and-drop reordering between columns (SortableJS)
- **Someday backlog** — uncompleted Someday tasks carry forward each week
- **Trello-style card panel** — click any task to open a side panel with all actions
  - Editable task name
  - Notes textarea
  - Recurrence picker (Daily, Weekly, Weekdays, Biweekly, Monthly, Annually, or custom `Every N days/weeks/months/years/Sun–Sat`)
  - Defer to Tomorrow / Next week / Someday
  - Duplicate, Attach file, Delete
- **Collapse day columns** — hide past days to a thin strip (desktop), persisted to localStorage
- **Global recurring tasks** — loaded from `WeeklyRecurring.md`, displayed in every column as read-only indicators
- **PIN authentication** with lockout after failed attempts
- **Obsidian-sync** — task files are plain markdown in the vault, editable from Obsidian on any device
- **Responsive** — flat list layout on mobile with larger tap targets and checkboxes on the right

## Stack

- Python / Flask + Gunicorn
- Vanilla JS (no framework)
- SortableJS for drag-and-drop
- JetBrains Mono, purple `#b967ff` accent, scanlines aesthetic

## Vault Storage

Weekly files live at `vault/Journals/Weekly/YYYY-WXX.md`:

```markdown
# Week 9 — Feb 23–Mar 1

## Monday
- [ ] Task text [recur:daily][attach:file.md]
  Optional note line
- [x] Completed task

## Someday
- [ ] Backlog item
```

Global recurring tasks: `vault/Projects/Quanta/Templates/WeeklyRecurring.md`

## Setup

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # set VAULT_PATH, PIN_HASH, SECRET_KEY
python app.py
```

Runs on `http://localhost:5002`.

## Deploy to Pi

```bash
bash deploy/deploy-to-pi.sh
```

Service: `athena.service` (systemd), Pi path: `/opt/athena/`

## Architecture

See `CLAUDE.md` for code architecture, key patterns, and gotchas.
