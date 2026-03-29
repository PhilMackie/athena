"""WeekPlanDaemon - Weekly planner with SQLite storage.

Source of truth: data/athena.db
Recurring tasks: vault/Projects/Quanta/Templates/WeeklyRecurring.md
"""

import json
import re
import sqlite3
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
import config

DB_PATH = config.DATA_DIR / "athena.db"
RECURRING_FILE = config.TEMPLATES_DIR / "WeeklyRecurring.md"

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
SECTIONS = DAY_NAMES + ["Someday"]
SOMEDAY_WEEK = '1900-01-01'  # sentinel: Someday tasks are global, not week-specific
MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


# ── Database ──────────────────────────────────────────────────────────────────

def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """Create tables if they don't exist. Called at app startup."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id           TEXT PRIMARY KEY,
                week_monday  TEXT NOT NULL,
                section      TEXT NOT NULL,
                position     INTEGER NOT NULL,
                text         TEXT NOT NULL,
                checked      INTEGER NOT NULL DEFAULT 0,
                recur        TEXT,
                attachment   TEXT,
                note         TEXT,
                created_at   TEXT NOT NULL,
                completed_at TEXT,
                nra_binding  TEXT,
                dwm_binding  TEXT
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_tasks_week_section
            ON tasks (week_monday, section, position)
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS weeks_initialized (
                week_monday TEXT PRIMARY KEY
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS migrations (
                name TEXT PRIMARY KEY
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS birthdays (
                id           TEXT PRIMARY KEY,
                name         TEXT NOT NULL,
                birth_month  INTEGER NOT NULL,
                birth_day    INTEGER NOT NULL,
                birth_year   INTEGER,
                reminder_days INTEGER,
                created_at   TEXT NOT NULL
            )
        """)
        for col in ('nra_binding', 'dwm_binding'):
            try:
                conn.execute(f'ALTER TABLE tasks ADD COLUMN {col} TEXT')
            except Exception:
                pass
        for col in ('steps TEXT', 'block_id TEXT', 'color TEXT'):
            try:
                conn.execute(f'ALTER TABLE tasks ADD COLUMN {col}')
            except Exception:
                pass
        # Seed birthday data (one-time)
        if not conn.execute("SELECT 1 FROM migrations WHERE name='seed_birthdays'").fetchone():
            _seed_birthdays(conn)
            conn.execute("INSERT INTO migrations VALUES ('seed_birthdays')")
        # Migrate any Someday tasks that are scattered across week_monday values
        conn.execute(
            "UPDATE tasks SET week_monday=? WHERE section='Someday' AND week_monday!=?",
            (SOMEDAY_WEEK, SOMEDAY_WEEK)
        )
        # Release any tasks stuck in blocks
        conn.execute("UPDATE tasks SET block_id=NULL WHERE block_id IS NOT NULL")


# ── Helpers ───────────────────────────────────────────────────────────────────

def get_week_bounds(date_str: str) -> tuple:
    d = date.fromisoformat(date_str)
    monday = d - timedelta(days=d.weekday())
    sunday = monday + timedelta(days=6)
    return monday, sunday


def _get_monday(date_str: str) -> str:
    monday, _ = get_week_bounds(date_str)
    return monday.isoformat()


def _get_task_by_idx(conn, week_monday: str, section: str, section_idx: int):
    wm = SOMEDAY_WEEK if section == 'Someday' else week_monday
    tasks = conn.execute(
        "SELECT * FROM tasks WHERE week_monday=? AND section=? ORDER BY position",
        (wm, section),
    ).fetchall()
    if section_idx < 0 or section_idx >= len(tasks):
        return None
    return tasks[section_idx]


def _normalize_positions(conn, week_monday: str, section: str):
    wm = SOMEDAY_WEEK if section == 'Someday' else week_monday
    tasks = conn.execute(
        "SELECT id FROM tasks WHERE week_monday=? AND section=? ORDER BY position",
        (wm, section),
    ).fetchall()
    for i, row in enumerate(tasks):
        conn.execute("UPDATE tasks SET position=? WHERE id=?", (i, row["id"]))


def _row_to_task(row, section_idx: int) -> dict:
    return {
        "type":        "task",
        "text":        row["text"],
        "checked":     bool(row["checked"]),
        "section_idx": section_idx,
        "recur":       row["recur"],
        "attachment":  row["attachment"],
        "note":        row["note"],
        "nra_binding": row["nra_binding"],
        "dwm_binding": row["dwm_binding"],
        "steps":       json.loads(row["steps"]) if row["steps"] else [],
        "color":       row["color"],
    }




# ── Recurring global tasks ────────────────────────────────────────────────────

def load_recurring() -> list:
    if not RECURRING_FILE.exists():
        return []
    items = []
    for line in RECURRING_FILE.read_text(encoding="utf-8").split("\n"):
        stripped = line.strip()
        if stripped.startswith("- "):
            text = stripped[2:].strip()
            if text:
                items.append(text)
    return items


# ── Week data ─────────────────────────────────────────────────────────────────

_DAY_ABBR = {'Sun': 'Sunday', 'Mon': 'Monday', 'Tue': 'Tuesday',
             'Wed': 'Wednesday', 'Thu': 'Thursday', 'Fri': 'Friday', 'Sat': 'Saturday'}
_WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday']


def _sections_for_recur(original_section: str, recur: str) -> list:
    """Return the day sections a task should appear in for the new week."""
    if not recur or original_section == 'Someday':
        return [original_section]
    if recur == 'daily':
        return DAY_NAMES[:]
    if recur == 'weekdays':
        return _WEEKDAYS[:]
    m = re.match(r'^every (Sun|Mon|Tue|Wed|Thu|Fri|Sat)$', recur, re.IGNORECASE)
    if m:
        return [_DAY_ABBR.get(m.group(1).capitalize(), original_section)]
    return [original_section]


def _interval_days(recur: str) -> int | None:
    """Days between occurrences for interval-based recurrence.
    Returns None for section-based patterns (daily, weekdays, every Mon, etc.)
    that should be carried every week regardless."""
    if not recur:
        return None
    r = recur.lower().strip()
    if r == 'weekly':
        return 7
    if r == 'biweekly':
        return 14
    if r == 'monthly':
        return 30
    if r == 'annually':
        return 365
    m = re.match(r'^every (\d+) (days?|weeks?|months?|years?)$', r)
    if m:
        n = int(m.group(1))
        unit = m.group(2).rstrip('s')
        return n * {'day': 1, 'week': 7, 'month': 30, 'year': 365}[unit]
    return None  # section-based — carry every week


def _should_carry(conn, text: str, section: str, recur: str, target_monday: str) -> bool:
    """For interval-based recurrence, check if target_monday falls on a cycle.
    Uses the earliest known occurrence as the reference point."""
    interval = _interval_days(recur)
    if interval is None or interval <= 7:
        return True  # section-based or weekly — always carry
    row = conn.execute(
        "SELECT MIN(week_monday) FROM tasks WHERE text=? AND section=? AND recur=?",
        (text, section, recur),
    ).fetchone()
    if not row or not row[0]:
        return True
    earliest = date.fromisoformat(row[0])
    target = date.fromisoformat(target_monday)
    days_since = (target - earliest).days
    if days_since < 0:
        return False
    # Carry if a cycle day falls within this 7-day week window
    return days_since % interval < 7


def get_or_create_week(date_str: str) -> dict:
    monday, sunday = get_week_bounds(date_str)
    week_monday = monday.isoformat()

    with get_db() as conn:
        carry_done = conn.execute(
            "SELECT 1 FROM weeks_initialized WHERE week_monday=?", (week_monday,)
        ).fetchone()

        if not carry_done:
            # Collect the most recent instance of each unique recurring task,
            # scanning back up to 52 weeks. Stopping at the first week with
            # any recurring tasks misses longer-interval tasks (e.g. biweekly)
            # that were skipped in the most recent week.
            seen = {}  # (text, section, recur) -> row
            for weeks_back in range(1, 53):
                candidate = (monday - timedelta(days=7 * weeks_back)).isoformat()
                rows = conn.execute(
                    """SELECT * FROM tasks WHERE week_monday=? AND recur IS NOT NULL
                       ORDER BY section, position""",
                    (candidate,),
                ).fetchall()
                for row in rows:
                    key = (row["text"], row["section"], row["recur"])
                    if key not in seen:
                        seen[key] = row
            carry = list(seen.values())

            now = datetime.utcnow().isoformat()
            inserted = set()

            for t in carry:
                if not _should_carry(conn, t["text"], t["section"], t["recur"], week_monday):
                    continue
                for target_section in _sections_for_recur(t["section"], t["recur"]):
                    key = (t["text"], target_section)
                    if key in inserted:
                        continue
                    already = conn.execute(
                        "SELECT 1 FROM tasks WHERE week_monday=? AND section=? AND text=?",
                        (week_monday, target_section, t["text"]),
                    ).fetchone()
                    if already:
                        inserted.add(key)
                        continue
                    inserted.add(key)
                    conn.execute(
                        """INSERT INTO tasks
                           (id, week_monday, section, position, text, checked,
                            recur, attachment, note, created_at, nra_binding, dwm_binding, steps)
                           VALUES (?,?,?,?,?,0,?,?,?,?,?,?,?)""",
                        (str(uuid.uuid4()), week_monday, target_section, 999,
                         t["text"], t["recur"], t["attachment"], t["note"], now,
                         t["nra_binding"], t["dwm_binding"], t["steps"]),
                    )
            for section in DAY_NAMES:
                _normalize_positions(conn, week_monday, section)
            conn.execute(
                "INSERT OR IGNORE INTO weeks_initialized VALUES (?)", (week_monday,)
            )

        # Birthday reminders — runs every load so newly-set reminders appear immediately
        now_bday = datetime.utcnow().isoformat()
        bdays = conn.execute(
            "SELECT * FROM birthdays WHERE reminder_days IS NOT NULL"
        ).fetchall()
        for bday in bdays:
            for yr_offset in (0, 1):
                try:
                    bday_date = date(monday.year + yr_offset,
                                     bday['birth_month'], bday['birth_day'])
                except ValueError:
                    continue
                reminder_date = bday_date - timedelta(days=bday['reminder_days'])
                if monday <= reminder_date <= sunday:
                    day_name = DAY_NAMES[reminder_date.weekday()]
                    n = bday['reminder_days']
                    if n == 0:
                        text = f"🎂 {bday['name']}'s birthday today!"
                    elif n == 1:
                        text = f"🎂 {bday['name']}'s birthday tomorrow"
                    else:
                        text = f"🎂 {bday['name']}'s birthday in {n} days"
                    already = conn.execute(
                        "SELECT 1 FROM tasks WHERE week_monday=? AND section=? AND text=?",
                        (week_monday, day_name, text),
                    ).fetchone()
                    if not already:
                        conn.execute(
                            """INSERT INTO tasks
                               (id, week_monday, section, position, text, checked, created_at)
                               VALUES (?,?,?,999,?,0,?)""",
                            (str(uuid.uuid4()), week_monday, day_name, text, now_bday),
                        )
                        _normalize_positions(conn, week_monday, day_name)
                    break

        rows = conn.execute(
            "SELECT * FROM tasks WHERE week_monday=? AND section!='Someday' ORDER BY section, position",
            (week_monday,),
        ).fetchall()

        someday_rows = conn.execute(
            "SELECT * FROM tasks WHERE section='Someday' ORDER BY position",
        ).fetchall()

    by_section = {s: [] for s in DAY_NAMES}
    for row in rows:
        if row["section"] in by_section:
            by_section[row["section"]].append(row)

    indexed = {
        s: [_row_to_task(r, i) for i, r in enumerate(by_section[s])]
        for s in DAY_NAMES
    }
    someday_indexed = [_row_to_task(r, i) for i, r in enumerate(someday_rows)]

    today = date.today()
    week_num = monday.isocalendar()[1]

    days = []
    for i, name in enumerate(DAY_NAMES):
        d = monday + timedelta(days=i)
        days.append({
            "name":     name,
            "short":    name[:3].upper(),
            "date_num": d.day,
            "date_str": d.isoformat(),
            "is_today": d == today,
            "items":    indexed[name],
        })

    return {
        "week_label": (
            f"Week {week_num} — "
            f"{MONTH_ABBR[monday.month-1]} {monday.day}–"
            f"{MONTH_ABBR[sunday.month-1]} {sunday.day}"
        ),
        "week_num": week_num,
        "monday":   week_monday,
        "sunday":   sunday.isoformat(),
        "header": (
            f"{MONTH_ABBR[monday.month-1]} {monday.day}–"
            f"{MONTH_ABBR[sunday.month-1]} {sunday.day}"
        ),
        "days":      days,
        "someday":   someday_indexed,
        "recurring": load_recurring(),
    }


# ── CRUD ──────────────────────────────────────────────────────────────────────

def add_task(date_str: str, section: str, text: str,
             recur: str = None, note: str = None) -> dict:
    if section not in SECTIONS:
        return {"error": f"Invalid section: {section}"}
    text = text.strip()
    if not text:
        return {"error": "Task text required"}
    week_monday = SOMEDAY_WEEK if section == 'Someday' else _get_monday(date_str)
    now = datetime.utcnow().isoformat()
    with get_db() as conn:
        max_pos = conn.execute(
            "SELECT COALESCE(MAX(position), -1) FROM tasks WHERE week_monday=? AND section=?",
            (week_monday, section),
        ).fetchone()[0]
        conn.execute(
            """INSERT INTO tasks
               (id, week_monday, section, position, text, checked,
                recur, attachment, note, created_at)
               VALUES (?,?,?,?,?,0,?,NULL,?,?)""",
            (str(uuid.uuid4()), week_monday, section, max_pos + 1,
             text, recur or None, note.strip() if note else None, now),
        )
    return {"status": "saved"}


def toggle_task(date_str: str, section: str, section_idx: int, checked: bool) -> dict:
    if section not in SECTIONS:
        return {"error": "Invalid section"}
    week_monday = _get_monday(date_str)
    with get_db() as conn:
        task = _get_task_by_idx(conn, week_monday, section, section_idx)
        if not task:
            return {"error": "Task not found"}
        completed_at = datetime.utcnow().isoformat() if checked else None
        conn.execute(
            "UPDATE tasks SET checked=?, completed_at=? WHERE id=?",
            (1 if checked else 0, completed_at, task["id"]),
        )
        nra_binding = task["nra_binding"] if checked else None
        dwm_binding = task["dwm_binding"] if checked else None
    result = {"status": "saved"}
    if nra_binding:
        result["nra_binding"] = nra_binding
    if dwm_binding:
        result["dwm_binding"] = dwm_binding
    return result


def delete_task(date_str: str, section: str, section_idx: int) -> dict:
    if section not in SECTIONS:
        return {"error": "Invalid section"}
    week_monday = _get_monday(date_str)
    with get_db() as conn:
        task = _get_task_by_idx(conn, week_monday, section, section_idx)
        if not task:
            return {"error": "Task not found"}
        conn.execute("DELETE FROM tasks WHERE id=?", (task["id"],))
        _normalize_positions(conn, week_monday, section)
    return {"status": "saved"}


def delete_task_all_future(date_str: str, section: str, section_idx: int) -> dict:
    """Delete this recurring task and all future instances with the same text."""
    if section not in SECTIONS:
        return {"error": "Invalid section"}
    week_monday = _get_monday(date_str)
    with get_db() as conn:
        task = _get_task_by_idx(conn, week_monday, section, section_idx)
        if not task:
            return {"error": "Task not found"}
        conn.execute(
            "DELETE FROM tasks WHERE text=? AND recur IS NOT NULL AND week_monday >= ?",
            (task["text"], week_monday),
        )
    return {"status": "saved"}


def set_task_recur(date_str: str, section: str, section_idx: int, recur: str) -> dict:
    if section not in SECTIONS:
        return {"error": "Invalid section"}
    week_monday = _get_monday(date_str)
    with get_db() as conn:
        task = _get_task_by_idx(conn, week_monday, section, section_idx)
        if not task:
            return {"error": "Task not found"}
        recur_val = recur.strip() or None
        old_recur = task["recur"]

        # Remove sibling copies from the current week that were created by the old recur pattern
        if old_recur and old_recur != recur_val and section != 'Someday':
            old_siblings = _sections_for_recur(section, old_recur)
            stale_sections = [s for s in old_siblings if s != section]
            for s in stale_sections:
                conn.execute(
                    "DELETE FROM tasks WHERE week_monday=? AND section=? AND text=? AND recur=?",
                    (week_monday, s, task["text"], old_recur),
                )
                _normalize_positions(conn, week_monday, s)

        conn.execute("UPDATE tasks SET recur=? WHERE id=?", (recur_val, task["id"]))

        # Expand to other sections of the same week immediately
        if recur_val and section != 'Someday':
            now = datetime.utcnow().isoformat()
            for target_section in _sections_for_recur(section, recur_val):
                if target_section == section:
                    continue
                exists = conn.execute(
                    "SELECT 1 FROM tasks WHERE week_monday=? AND section=? AND text=?",
                    (week_monday, target_section, task["text"]),
                ).fetchone()
                if not exists:
                    conn.execute(
                        """INSERT INTO tasks
                           (id, week_monday, section, position, text, checked,
                            recur, attachment, note, created_at, nra_binding, dwm_binding)
                           VALUES (?,?,?,?,?,0,?,?,?,?,?,?)""",
                        (str(uuid.uuid4()), week_monday, target_section, 999,
                         task["text"], recur_val, task["attachment"], task["note"],
                         now, task["nra_binding"], task["dwm_binding"]),
                    )
                    _normalize_positions(conn, week_monday, target_section)
    return {"status": "saved"}


def rename_task(date_str: str, section: str, section_idx: int, new_text: str) -> dict:
    if section not in SECTIONS:
        return {"error": "Invalid section"}
    new_text = new_text.strip()
    if not new_text:
        return {"error": "Task text required"}
    week_monday = _get_monday(date_str)
    with get_db() as conn:
        task = _get_task_by_idx(conn, week_monday, section, section_idx)
        if not task:
            return {"error": "Task not found"}
        conn.execute("UPDATE tasks SET text=? WHERE id=?", (new_text, task["id"]))
    return {"status": "saved"}


def set_task_note(date_str: str, section: str, section_idx: int, note: str) -> dict:
    if section not in SECTIONS:
        return {"error": "Invalid section"}
    week_monday = _get_monday(date_str)
    with get_db() as conn:
        task = _get_task_by_idx(conn, week_monday, section, section_idx)
        if not task:
            return {"error": "Task not found"}
        conn.execute("UPDATE tasks SET note=? WHERE id=?",
                     (note.strip() or None, task["id"]))
    return {"status": "saved"}


def attach_file(date_str: str, section: str, section_idx: int, filename: str) -> dict:
    if section not in SECTIONS:
        return {"error": "Invalid section"}
    week_monday = _get_monday(date_str)
    with get_db() as conn:
        task = _get_task_by_idx(conn, week_monday, section, section_idx)
        if not task:
            return {"error": "Task not found"}
        conn.execute("UPDATE tasks SET attachment=? WHERE id=?",
                     (filename.strip() or None, task["id"]))
    return {"status": "saved"}


def defer_task(date_str: str, section: str, section_idx: int, defer_to: str) -> dict:
    if section not in SECTIONS:
        return {"error": "Invalid section"}
    week_monday = _get_monday(date_str)

    with get_db() as conn:
        task = _get_task_by_idx(conn, week_monday, section, section_idx)
        if not task:
            return {"error": "Task not found"}

        if defer_to == "someday":
            if section == "Someday":
                return {"error": "Already in Someday"}
            target_monday, target_section = SOMEDAY_WEEK, "Someday"

        elif defer_to == "tomorrow":
            if section == "Someday":
                return {"error": "Cannot defer Someday task to tomorrow"}
            tomorrow = date.fromisoformat(date_str) + timedelta(days=1)
            target_monday = _get_monday(tomorrow.isoformat())
            target_section = tomorrow.strftime("%A")

        elif defer_to == "next_week":
            if section == "Someday":
                return {"error": "Already in Someday backlog"}
            next_mon = date.fromisoformat(week_monday) + timedelta(days=7)
            target_monday, target_section = next_mon.isoformat(), "Monday"

        elif defer_to == "weekend":
            if section == "Someday":
                return {"error": "Cannot defer Someday task to weekend"}
            target_monday, target_section = week_monday, "Saturday"

        else:
            return {"error": f"Unknown defer_to: {defer_to}"}

        old_block_id = task["block_id"]
        max_pos = conn.execute(
            "SELECT COALESCE(MAX(position), -1) FROM tasks WHERE week_monday=? AND section=? AND block_id IS NULL",
            (target_monday, target_section),
        ).fetchone()[0]
        conn.execute(
            "UPDATE tasks SET week_monday=?, section=?, position=?, checked=0, block_id=NULL WHERE id=?",
            (target_monday, target_section, max_pos + 1, task["id"]),
        )
        if old_block_id:
            _normalize_block_positions(conn, old_block_id)
        _normalize_positions(conn, week_monday, section)
        _normalize_positions(conn, target_monday, target_section)

    return {"status": "saved"}


def duplicate_task(date_str: str, section: str, section_idx: int) -> dict:
    if section not in SECTIONS:
        return {"error": "Invalid section"}
    week_monday = _get_monday(date_str)
    with get_db() as conn:
        task = _get_task_by_idx(conn, week_monday, section, section_idx)
        if not task:
            return {"error": "Task not found"}
        now = datetime.utcnow().isoformat()
        block_id = task["block_id"]
        if block_id:
            conn.execute(
                "UPDATE tasks SET position=position+1 WHERE block_id=? AND position>?",
                (block_id, task["position"]),
            )
            conn.execute(
                """INSERT INTO tasks
                   (id, week_monday, section, position, text, checked,
                    recur, attachment, note, created_at, block_id)
                   VALUES (?,?,?,?,?,0,?,?,?,?,?)""",
                (str(uuid.uuid4()), week_monday, section, task["position"] + 1,
                 task["text"], task["recur"], task["attachment"], task["note"], now, block_id),
            )
            _normalize_block_positions(conn, block_id)
        else:
            conn.execute(
                "UPDATE tasks SET position=position+1 WHERE week_monday=? AND section=? AND block_id IS NULL AND position>?",
                (week_monday, section, task["position"]),
            )
            conn.execute(
                """INSERT INTO tasks
                   (id, week_monday, section, position, text, checked,
                    recur, attachment, note, created_at)
                   VALUES (?,?,?,?,?,0,?,?,?,?)""",
                (str(uuid.uuid4()), week_monday, section, task["position"] + 1,
                 task["text"], task["recur"], task["attachment"], task["note"], now),
            )
            _normalize_positions(conn, week_monday, section)
    return {"status": "saved"}


def set_task_binding(date_str: str, section: str, section_idx: int, binding_type: str, value: str) -> dict:
    if section not in SECTIONS:
        return {"error": "Invalid section"}
    if binding_type not in ('nra_binding', 'dwm_binding'):
        return {"error": "Invalid binding type"}
    week_monday = _get_monday(date_str)
    with get_db() as conn:
        task = _get_task_by_idx(conn, week_monday, section, section_idx)
        if not task:
            return {"error": "Task not found"}
        conn.execute(f"UPDATE tasks SET {binding_type}=? WHERE id=?",
                     (value.strip() or None, task["id"]))
    return {"status": "saved"}


def set_task_color(date_str: str, section: str, section_idx: int, color: str) -> dict:
    if section not in SECTIONS:
        return {"error": "Invalid section"}
    week_monday = _get_monday(date_str)
    with get_db() as conn:
        task = _get_task_by_idx(conn, week_monday, section, section_idx)
        if not task:
            return {"error": "Task not found"}
        conn.execute("UPDATE tasks SET color=? WHERE id=?",
                     (color.strip() or None, task["id"]))
    return {"status": "saved"}


def toggle_step(date_str: str, section: str, section_idx: int, step_idx: int, checked: bool) -> dict:
    if section not in SECTIONS:
        return {"error": "Invalid section"}
    week_monday = _get_monday(date_str)
    with get_db() as conn:
        task = _get_task_by_idx(conn, week_monday, section, section_idx)
        if not task:
            return {"error": "Task not found"}
        steps = json.loads(task["steps"]) if task["steps"] else []
        if step_idx < 0 or step_idx >= len(steps):
            return {"error": "Step not found"}
        steps[step_idx] = 1 if checked else 0
        all_done = len(steps) > 0 and all(steps)
        conn.execute("UPDATE tasks SET steps=? WHERE id=?",
                     (json.dumps(steps), task["id"]))
    return {"status": "saved", "auto_complete": all_done}


def set_step_count(date_str: str, section: str, section_idx: int, count: int) -> dict:
    if section not in SECTIONS:
        return {"error": "Invalid section"}
    count = max(0, int(count))
    week_monday = _get_monday(date_str)
    with get_db() as conn:
        task = _get_task_by_idx(conn, week_monday, section, section_idx)
        if not task:
            return {"error": "Task not found"}
        existing = json.loads(task["steps"]) if task["steps"] else []
        if count > len(existing):
            new_steps = existing + [0] * (count - len(existing))
        else:
            new_steps = existing[:count]
        value = json.dumps(new_steps) if new_steps else None
        conn.execute("UPDATE tasks SET steps=? WHERE id=?", (value, task["id"]))
    return {"status": "saved"}


def reorder_section(date_str: str, section: str, ordered_texts: list) -> dict:
    if section not in SECTIONS:
        return {"error": "Invalid section"}
    week_monday = SOMEDAY_WEEK if section == 'Someday' else _get_monday(date_str)
    with get_db() as conn:
        if section == 'Someday':
            tasks = conn.execute(
                "SELECT * FROM tasks WHERE section='Someday' ORDER BY position",
            ).fetchall()
        else:
            tasks = conn.execute(
                "SELECT * FROM tasks WHERE week_monday=? AND section=? ORDER BY position",
                (week_monday, section),
            ).fetchall()
        text_to_id = {}
        for t in tasks:
            if t["text"] not in text_to_id:
                text_to_id[t["text"]] = t["id"]
        for i, text in enumerate(ordered_texts):
            if text in text_to_id:
                conn.execute("UPDATE tasks SET position=? WHERE id=?",
                             (i, text_to_id[text]))
        _normalize_positions(conn, week_monday, section)
    return {"status": "saved"}


# ── Birthdays ─────────────────────────────────────────────────────────────────

def _seed_birthdays(conn):
    now = datetime.utcnow().isoformat()
    seed = [
        ("Harrison",      1, 22, 2023),
        ("Matt Robertson",1, 26, 1992),
        ("Tank",          1, 12, 2023),
        ("Dani",          1,  8, 2016),
        ("Dad",           2,  5, 1958),
        ("Sean",          3, 16, 1987),
        ("Jeff",          3, 12, 1987),
        ("Drew",          3, 20, 1998),
        ("Eve",           3,  5, 1959),
        ("Shelly",        4,  1, 1995),
        ("Hunter",        4,  9, 1990),
        ("Seth",          4, 22, 1987),
        ("Justin",        4, 23, 1987),
        ("Grandma",       4,  3, 1936),
        ("Kelsie",        4, 30, 1995),
        ("Austin",        5, 12, 1990),
        ("Nate",          7, 20, 1992),
        ("Olivia",        8, 15, 1929),
        ("Noah",          8, 17, 1987),
        ("Chris B",       9, 11, None),
        ("Vyasar",        9, 14, 1990),
        ("Will",         11, 20, 1992),
        ("Charlie",      12, 13, 1999),
        ("Mason",        12, 21, 2013),
        ("Matt Mackie",  12, 27, 1994),
    ]
    for name, month, day, year in seed:
        conn.execute(
            """INSERT INTO birthdays
               (id, name, birth_month, birth_day, birth_year, created_at)
               VALUES (?,?,?,?,?,?)""",
            (str(uuid.uuid4()), name, month, day, year, now),
        )


def list_birthdays() -> list:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM birthdays ORDER BY birth_month, birth_day, name"
        ).fetchall()
    return [dict(r) for r in rows]


def add_birthday(name: str, birth_month: int, birth_day: int,
                 birth_year: int = None, reminder_days: int = None) -> dict:
    name = name.strip()
    if not name:
        return {"error": "Name required"}
    if not (1 <= birth_month <= 12 and 1 <= birth_day <= 31):
        return {"error": "Invalid date"}
    now = datetime.utcnow().isoformat()
    with get_db() as conn:
        conn.execute(
            """INSERT INTO birthdays
               (id, name, birth_month, birth_day, birth_year, reminder_days, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (str(uuid.uuid4()), name, birth_month, birth_day, birth_year, reminder_days, now),
        )
    return {"status": "saved"}


def delete_birthday(birthday_id: str) -> dict:
    with get_db() as conn:
        conn.execute("DELETE FROM birthdays WHERE id=?", (birthday_id,))
    return {"status": "deleted"}


def bulk_set_birthday_reminder(ids: list, reminder_days) -> dict:
    with get_db() as conn:
        for bid in ids:
            conn.execute(
                "UPDATE birthdays SET reminder_days=? WHERE id=?",
                (reminder_days, bid),
            )
    return {"status": "saved", "count": len(ids)}


