#!/usr/bin/env python3
"""One-time migration: import existing markdown week files into SQLite.

Run once on the Pi after deploying the SQLite version:
    cd /opt/athena
    venv/bin/python migrate.py
"""

import re
import sys
import uuid
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import config
from daemons.weekplan import (
    init_db, get_db, _normalize_positions,
    SECTIONS, DAY_NAMES,
)

WEEKPLAN_DIR = config.OBSIDIAN_VAULT / "Journals" / "Weekly"


def parse_md(path: Path) -> dict:
    sections = {s: [] for s in SECTIONS}
    current = None
    for line in path.read_text(encoding="utf-8").split("\n"):
        if line.startswith("## "):
            name = line[3:].strip()
            current = name if name in SECTIONS else None
        elif current is not None:
            if line.startswith("  ") and sections[current]:
                note_text = line[2:].strip()
                if note_text:
                    last = sections[current][-1]
                    existing = last.get("note") or ""
                    last["note"] = (existing + "\n" + note_text).strip() if existing else note_text
            else:
                m = re.match(r'^- \[([ xX])\] (.+)$', line)
                if m:
                    checked = m.group(1).lower() == 'x'
                    raw = m.group(2)
                    recur_m  = re.search(r'\[recur:([^\]]+)\]', raw)
                    attach_m = re.search(r'\[attach:([^\]]+)\]', raw)
                    clean = re.sub(r'\s*\[(?:recur|attach):[^\]]+\]', '', raw).strip()
                    sections[current].append({
                        "text":       clean,
                        "checked":    checked,
                        "recur":      recur_m.group(1)  if recur_m  else None,
                        "attachment": attach_m.group(1) if attach_m else None,
                        "note":       None,
                    })
    return sections


def migrate():
    init_db()

    with get_db() as conn:
        existing = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        if existing > 0:
            print(f"Database already has {existing} tasks — skipping migration.")
            print("Delete data/athena.db to force a re-migration.")
            return

    md_files = sorted(WEEKPLAN_DIR.glob("*.md")) if WEEKPLAN_DIR.exists() else []
    if not md_files:
        print("No markdown files found — starting fresh.")
        return

    now = datetime.utcnow().isoformat()
    total_tasks = 0

    for md_path in md_files:
        try:
            name = md_path.stem          # e.g. "2026-W09"
            year_s, week_s = name.split("-W")
            monday = date.fromisocalendar(int(year_s), int(week_s), 1)
            week_monday = monday.isoformat()
        except Exception as e:
            print(f"  Skipping {md_path.name}: can't parse filename ({e})")
            continue

        sections = parse_md(md_path)
        week_count = 0

        with get_db() as conn:
            for section in SECTIONS:
                for pos, task in enumerate(sections.get(section, [])):
                    conn.execute(
                        """INSERT INTO tasks
                           (id, week_monday, section, position, text, checked,
                            recur, attachment, note, created_at)
                           VALUES (?,?,?,?,?,?,?,?,?,?)""",
                        (str(uuid.uuid4()), week_monday, section, pos,
                         task["text"], 1 if task["checked"] else 0,
                         task.get("recur"), task.get("attachment"),
                         task.get("note"), now),
                    )
                    week_count += 1

        print(f"  {week_monday}: {week_count} tasks")
        total_tasks += week_count

    print(f"\nDone — {total_tasks} tasks imported from {len(md_files)} week files.")


if __name__ == "__main__":
    migrate()
