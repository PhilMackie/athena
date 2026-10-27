"""WeekPlanDaemon - Weekly planner with SQLite storage.

Source of truth: data/athena.db
Recurring tasks: vault/Projects/Quanta/Templates/WeeklyRecurring.md
"""

import calendar as _calendar
import csv
import io
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
SECTIONS = DAY_NAMES + ["Scratchpad", "Someday"]
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


def _migrate_forward_fill_recurring(conn):
    """Inject recurring tasks into all weeks — initialized or not — for the next 12 weeks."""
    today = date.today()
    current_monday = today - timedelta(days=today.weekday())

    initialized = {
        r[0] for r in conn.execute("SELECT week_monday FROM weeks_initialized").fetchall()
    }
    target_weeks = sorted(
        initialized |
        {(current_monday + timedelta(days=7 * i)).isoformat() for i in range(13)}
    )

    # Deduplicate by (text, recur) for monthly tasks; (text, section, recur) for others
    seen = {}
    all_recur_rows = conn.execute(
        "SELECT * FROM tasks WHERE recur IS NOT NULL ORDER BY week_monday, created_at"
    ).fetchall()
    for row in all_recur_rows:
        if row["section"] in ('Someday', 'Scratchpad'):
            continue
        n_months = _month_interval(row["recur"])
        key = (row["text"], row["recur"]) if n_months else (row["text"], row["section"], row["recur"])
        seen[key] = row  # last wins (latest week/created_at)

    now = datetime.utcnow().isoformat()
    for key, task in seen.items():
        text, recur = task["text"], task["recur"]
        section = task["section"]
        origin_week = task["week_monday"]
        n_months = _month_interval(recur)

        origin = date.fromisoformat(task["created_at"][:10]) if n_months else None

        for fw in target_weeks:
            if fw <= origin_week:
                continue
            if n_months is not None:
                target_section = _section_for_monthly(origin, n_months, fw)
                if target_section is None:
                    continue
                sections_for_fw = [target_section]
            else:
                sections_for_fw = [s for s in _sections_for_recur(section, recur)
                                   if _should_carry(conn, text, s, recur, fw)]
            for target_section in sections_for_fw:
                already = conn.execute(
                    "SELECT 1 FROM tasks WHERE week_monday=? AND section=? AND text=?",
                    (fw, target_section, text),
                ).fetchone()
                if already:
                    continue
                fw_pos = conn.execute(
                    "SELECT MAX(position) FROM tasks WHERE week_monday=? AND section=?",
                    (fw, target_section),
                ).fetchone()[0]
                conn.execute(
                    """INSERT INTO tasks
                       (id, week_monday, section, position, text, checked,
                        recur, attachment, note, created_at, nra_binding, dwm_binding, steps, color)
                       VALUES (?,?,?,?,?,0,?,?,?,?,?,?,?,?)""",
                    (str(uuid.uuid4()), fw, target_section, (fw_pos or 0) + 1,
                     text, recur, task["attachment"], task["note"], now,
                     task["nra_binding"], task["dwm_binding"], task["steps"], task["color"]),
                )


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
        for col in ('steps TEXT', 'block_id TEXT', 'color TEXT', 'source TEXT'):
            try:
                conn.execute(f'ALTER TABLE tasks ADD COLUMN {col}')
            except Exception:
                pass
        try:
            conn.execute('ALTER TABLE birthdays ADD COLUMN reminder_offsets TEXT')
        except Exception:
            pass
        # migrate old single reminder_days value into new reminder_offsets column
        conn.execute("""
            UPDATE birthdays SET reminder_offsets = CAST(reminder_days AS TEXT)
            WHERE reminder_offsets IS NULL AND reminder_days IS NOT NULL
        """)
        # Seed birthday data (one-time)
        if not conn.execute("SELECT 1 FROM migrations WHERE name='seed_birthdays'").fetchone():
            _seed_birthdays(conn)
            conn.execute("INSERT INTO migrations VALUES ('seed_birthdays')")
        # One-time: set all existing birthdays to standard 0,7 reminders
        if not conn.execute("SELECT 1 FROM migrations WHERE name='bday_reminders_0_7'").fetchone():
            conn.execute("UPDATE birthdays SET reminder_offsets='0,7'")
            conn.execute("INSERT INTO migrations VALUES ('bday_reminders_0_7')")
        # Migrate any Someday tasks that are scattered across week_monday values
        conn.execute(
            "UPDATE tasks SET week_monday=? WHERE section='Someday' AND week_monday!=?",
            (SOMEDAY_WEEK, SOMEDAY_WEEK)
        )
        # Retroactively forward-fill recurring tasks into already-initialized weeks
        if not conn.execute("SELECT 1 FROM migrations WHERE name='forward_fill_recurring_v3'").fetchone():
            _migrate_forward_fill_recurring(conn)
            conn.execute("INSERT INTO migrations VALUES ('forward_fill_recurring_v3')")


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
        "block_id":    row["block_id"],
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


def _section_for_monthly(origin: date, n_months: int, target_monday: str) -> str | None:
    """Return the day-column where the monthly occurrence falls in target_monday's week.
    June 11 is a Thursday → 'Thursday'. Returns None if no occurrence falls in that week."""
    target = date.fromisoformat(target_monday)
    target_sunday = target + timedelta(days=6)
    m = 0
    while True:
        candidate = _add_months(origin, m)
        if candidate > target_sunday:
            return None
        if candidate >= target:
            return DAY_NAMES[candidate.weekday()]
        m += n_months


def _add_months(d: date, months: int) -> date:
    """Add N calendar months, clamping day to end-of-month if needed."""
    total = d.month - 1 + months
    year = d.year + total // 12
    month = total % 12 + 1
    day = min(d.day, _calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _month_interval(recur: str):
    """Return number of calendar months for month/year recurrence patterns, else None."""
    r = recur.lower().strip()
    if r == 'monthly':
        return 1
    if r == 'annually':
        return 12
    m = re.match(r'^every (\d+) (months?|years?)$', r)
    if m:
        n = int(m.group(1))
        unit = m.group(2).rstrip('s')
        return n * (12 if unit == 'year' else 1)
    return None


def _interval_days(recur: str) -> int | None:
    """Days between occurrences for day/week interval-based recurrence.
    Returns None for section-based patterns (daily, weekdays, every Mon, etc.)
    or month/year patterns (handled separately by _month_interval)."""
    if not recur:
        return None
    r = recur.lower().strip()
    if r == 'weekly':
        return 7
    if r == 'biweekly':
        return 14
    m = re.match(r'^every (\d+) (days?|weeks?)$', r)
    if m:
        n = int(m.group(1))
        unit = m.group(2).rstrip('s')
        return n * {'day': 1, 'week': 7}[unit]
    return None  # section-based or month/year — handled elsewhere


def _should_carry(conn, text: str, section: str, recur: str, target_monday: str) -> bool:
    """Check if target_monday's week should receive a carry of this recurring task.
    Monthly/annual patterns use _section_for_monthly instead — not handled here."""
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
                if t["section"] == 'Scratchpad':
                    continue
                n_months = _month_interval(t["recur"])
                if n_months is not None:
                    # Find origin across all sections — monthly copies live in different columns
                    origin_row = conn.execute(
                        """SELECT created_at FROM tasks WHERE text=? AND recur=?
                           ORDER BY week_monday ASC, created_at ASC LIMIT 1""",
                        (t["text"], t["recur"]),
                    ).fetchone()
                    if not origin_row:
                        continue
                    origin = date.fromisoformat(origin_row[0][:10])
                    target_section = _section_for_monthly(origin, n_months, week_monday)
                    if target_section is None:
                        continue
                    sections_for_week = [target_section]
                else:
                    if not _should_carry(conn, t["text"], t["section"], t["recur"], week_monday):
                        continue
                    sections_for_week = _sections_for_recur(t["section"], t["recur"])
                for target_section in sections_for_week:
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
                    # Fresh (unchecked) steps for the new week, not the prior
                    # instance's in-progress checked state.
                    fresh_steps = (json.dumps([0] * len(json.loads(t["steps"])))
                                    if t["steps"] else None)
                    conn.execute(
                        """INSERT INTO tasks
                           (id, week_monday, section, position, text, checked,
                            recur, attachment, note, created_at, nra_binding, dwm_binding, steps, color)
                           VALUES (?,?,?,?,?,0,?,?,?,?,?,?,?,?)""",
                        (str(uuid.uuid4()), week_monday, target_section, 999,
                         t["text"], t["recur"], t["attachment"], t["note"], now,
                         t["nra_binding"], t["dwm_binding"], fresh_steps, t["color"]),
                    )
            for section in DAY_NAMES:
                _normalize_positions(conn, week_monday, section)
            conn.execute(
                "INSERT OR IGNORE INTO weeks_initialized VALUES (?)", (week_monday,)
            )

        # Birthday reminders — runs every load so newly-set reminders appear immediately
        now_bday = datetime.utcnow().isoformat()
        bdays = conn.execute("SELECT * FROM birthdays").fetchall()
        for bday in bdays:
            offsets_raw = bday['reminder_offsets']
            if not offsets_raw:
                continue
            offsets = [int(x.strip()) for x in offsets_raw.split(',')
                       if x.strip().lstrip('-').isdigit()]
            for offset in offsets:
                for yr_offset in (0, 1):
                    try:
                        bday_date = date(monday.year + yr_offset,
                                         bday['birth_month'], bday['birth_day'])
                    except ValueError:
                        continue
                    reminder_date = bday_date - timedelta(days=offset)
                    if monday <= reminder_date <= sunday:
                        day_name = DAY_NAMES[reminder_date.weekday()]
                        if offset == 0:
                            text = f"🎂 {bday['name']}'s birthday today!"
                        elif offset == 1:
                            text = f"🎂 {bday['name']}'s birthday tomorrow"
                        else:
                            text = f"🎂 {bday['name']}'s birthday in {offset} days"
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

        scratchpad_rows = conn.execute(
            "SELECT * FROM tasks WHERE week_monday=? AND section='Scratchpad' ORDER BY position",
            (week_monday,),
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
    scratchpad_indexed = [_row_to_task(r, i) for i, r in enumerate(scratchpad_rows)]

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
        "days":        days,
        "someday":     someday_indexed,
        "scratchpad":  scratchpad_indexed,
        "recurring":   load_recurring(),
    }


# ── CRUD ──────────────────────────────────────────────────────────────────────

def add_task(date_str: str, section: str, text: str,
             recur: str = None, note: str = None, source: str = None) -> dict:
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
                recur, attachment, note, created_at, source)
               VALUES (?,?,?,?,?,0,?,NULL,?,?,?)""",
            (str(uuid.uuid4()), week_monday, section, max_pos + 1,
             text, recur or None, note.strip() if note else None, now, source),
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
        # Clear recur on older instances too — otherwise get_or_create_week's
        # carry-forward scan (which looks up to 52 weeks back for any row with
        # recur set) resurrects the series the first time a not-yet-visited
        # future week is opened, undoing this delete.
        conn.execute(
            "UPDATE tasks SET recur=NULL WHERE text=? AND recur IS NOT NULL AND week_monday < ?",
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

        # Wipe stale forward copies across ALL sections before rebuilding
        if old_recur and section != 'Someday':
            conn.execute(
                "DELETE FROM tasks WHERE text=? AND recur=? AND week_monday > ? AND checked=0",
                (task["text"], old_recur, week_monday),
            )
            # Also clean up same-week siblings from old pattern
            if old_recur != recur_val:
                for s in _sections_for_recur(section, old_recur):
                    if s != section:
                        conn.execute(
                            "DELETE FROM tasks WHERE week_monday=? AND section=? AND text=? AND recur=?",
                            (week_monday, s, task["text"], old_recur),
                        )
                        _normalize_positions(conn, week_monday, s)

        conn.execute("UPDATE tasks SET recur=? WHERE id=?", (recur_val, task["id"]))

        # For month-interval patterns, anchor created_at to the task's actual calendar
        # date so _section_for_monthly places each copy on the correct weekday.
        # Task in Monday col of May-11 week → anchor May 11 → Jun 11=Thu, Jul 11=Sat, etc.
        n_months = _month_interval(recur_val) if recur_val else None
        day_offset = DAY_NAMES.index(section) if section in DAY_NAMES else 0
        if n_months is not None:
            task_date = (date.fromisoformat(week_monday) + timedelta(days=day_offset)).isoformat()
            conn.execute("UPDATE tasks SET created_at=? WHERE id=?",
                         (task_date + "T00:00:00", task["id"]))
            origin = date.fromisoformat(task_date)
        else:
            origin = None

        if recur_val and section != 'Someday':
            now = datetime.utcnow().isoformat()
            # Each recurring copy gets its own fresh (unchecked) set of steps,
            # not the original's in-progress checked state.
            fresh_steps = (json.dumps([0] * len(json.loads(task["steps"])))
                            if task["steps"] else None)
            # Expand to sibling sections in the current week (daily/weekdays patterns)
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
                            recur, attachment, note, created_at, nra_binding, dwm_binding, steps)
                           VALUES (?,?,?,?,?,0,?,?,?,?,?,?,?)""",
                        (str(uuid.uuid4()), week_monday, target_section, 999,
                         task["text"], recur_val, task["attachment"], task["note"],
                         now, task["nra_binding"], task["dwm_binding"], fresh_steps),
                    )
                    _normalize_positions(conn, week_monday, target_section)
            # Forward-fill next 52 weeks
            base = date.fromisoformat(week_monday)
            future_weeks = sorted(
                {r[0] for r in conn.execute(
                    "SELECT week_monday FROM weeks_initialized WHERE week_monday > ?",
                    (week_monday,),
                ).fetchall()} |
                {(base + timedelta(days=7 * i)).isoformat() for i in range(1, 53)}
            )
            for fw in future_weeks:
                if n_months is not None:
                    target_section = _section_for_monthly(origin, n_months, fw)
                    if target_section is None:
                        continue
                    sections_for_fw = [target_section]
                else:
                    sections_for_fw = [s for s in _sections_for_recur(section, recur_val)
                                       if _should_carry(conn, task["text"], s, recur_val, fw)]
                for target_section in sections_for_fw:
                    fw_already = conn.execute(
                        "SELECT 1 FROM tasks WHERE week_monday=? AND section=? AND text=?",
                        (fw, target_section, task["text"]),
                    ).fetchone()
                    if fw_already:
                        continue
                    fw_pos = conn.execute(
                        "SELECT MAX(position) FROM tasks WHERE week_monday=? AND section=?",
                        (fw, target_section),
                    ).fetchone()[0]
                    conn.execute(
                        """INSERT INTO tasks
                           (id, week_monday, section, position, text, checked,
                            recur, attachment, note, created_at, nra_binding, dwm_binding, color, steps)
                           VALUES (?,?,?,?,?,0,?,?,?,?,?,?,?,?)""",
                        (str(uuid.uuid4()), fw, target_section, (fw_pos or 0) + 1,
                         task["text"], recur_val, task["attachment"], task["note"],
                         now, task["nra_binding"], task["dwm_binding"], task["color"], fresh_steps),
                    )
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
            if section in DAY_NAMES:
                task_date = date.fromisoformat(week_monday) + timedelta(days=DAY_NAMES.index(section))
            else:
                task_date = date.fromisoformat(week_monday)
            tomorrow = task_date + timedelta(days=1)
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

        max_pos = conn.execute(
            "SELECT COALESCE(MAX(position), -1) FROM tasks WHERE week_monday=? AND section=?",
            (target_monday, target_section),
        ).fetchone()[0]
        conn.execute(
            "UPDATE tasks SET week_monday=?, section=?, position=?, checked=0 WHERE id=?",
            (target_monday, target_section, max_pos + 1, task["id"]),
        )
        _normalize_positions(conn, week_monday, section)
        _normalize_positions(conn, target_monday, target_section)

    return {"status": "saved"}


def purge_scratchpad_done(date_str: str) -> dict:
    """Move checked Scratchpad tasks into today's day column, still checked."""
    week_monday = _get_monday(date_str)
    today = date.today()
    target_monday = _get_monday(today.isoformat())
    target_section = DAY_NAMES[today.weekday()]

    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE week_monday=? AND section='Scratchpad' AND checked=1"
            " ORDER BY position",
            (week_monday,),
        ).fetchall()
        if not rows:
            return {"status": "done", "moved": 0}

        max_pos = conn.execute(
            "SELECT COALESCE(MAX(position), -1) FROM tasks WHERE week_monday=? AND section=?",
            (target_monday, target_section),
        ).fetchone()[0]
        for i, row in enumerate(rows):
            conn.execute(
                "UPDATE tasks SET week_monday=?, section=?, position=? WHERE id=?",
                (target_monday, target_section, max_pos + 1 + i, row["id"]),
            )
        _normalize_positions(conn, week_monday, 'Scratchpad')
        _normalize_positions(conn, target_monday, target_section)

    return {"status": "done", "moved": len(rows)}


def duplicate_task(date_str: str, section: str, section_idx: int) -> dict:
    if section not in SECTIONS:
        return {"error": "Invalid section"}
    week_monday = _get_monday(date_str)
    with get_db() as conn:
        task = _get_task_by_idx(conn, week_monday, section, section_idx)
        if not task:
            return {"error": "Task not found"}
        now = datetime.utcnow().isoformat()
        conn.execute(
            "UPDATE tasks SET position=position+1 WHERE week_monday=? AND section=? AND position>?",
            (week_monday, section, task["position"]),
        )
        conn.execute(
            """INSERT INTO tasks
               (id, week_monday, section, position, text, checked,
                recur, attachment, note, created_at, block_id)
               VALUES (?,?,?,?,?,0,?,?,?,?,?)""",
            (str(uuid.uuid4()), week_monday, section, task["position"] + 1,
             task["text"], task["recur"], task["attachment"], task["note"], now, task["block_id"]),
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
        val = value.strip() or None
        conn.execute(f"UPDATE tasks SET {binding_type}=? WHERE id=?", (val, task["id"]))
        if task["recur"]:
            conn.execute(
                f"UPDATE tasks SET {binding_type}=? WHERE text=? AND section=? AND recur=? AND week_monday > ?",
                (val, task["text"], section, task["recur"], week_monday),
            )
    return {"status": "saved"}


def set_task_color(date_str: str, section: str, section_idx: int, color: str) -> dict:
    if section not in SECTIONS:
        return {"error": "Invalid section"}
    week_monday = _get_monday(date_str)
    with get_db() as conn:
        task = _get_task_by_idx(conn, week_monday, section, section_idx)
        if not task:
            return {"error": "Task not found"}
        val = color.strip() or None
        conn.execute("UPDATE tasks SET color=? WHERE id=?", (val, task["id"]))
        if task["recur"]:
            conn.execute(
                "UPDATE tasks SET color=? WHERE text=? AND section=? AND recur=? AND week_monday > ?",
                (val, task["text"], section, task["recur"], week_monday),
            )
    return {"status": "saved"}


def set_task_block(date_str: str, section: str, section_idx: int, block_id: str) -> dict:
    """Tag/untag a task with a Time Block id. The task stays exactly where it
    is (day/Scratchpad/Someday) — this only controls whether it also shows up
    inside that block. block_id=None clears the tag."""
    if section not in SECTIONS:
        return {"error": "Invalid section"}
    week_monday = _get_monday(date_str)
    with get_db() as conn:
        task = _get_task_by_idx(conn, week_monday, section, section_idx)
        if not task:
            return {"error": "Task not found"}
        conn.execute("UPDATE tasks SET block_id=? WHERE id=?", (block_id or None, task["id"]))
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
                 birth_year: int = None, reminder_offsets: str = "0,7") -> dict:
    name = name.strip()
    if not name:
        return {"error": "Name required"}
    if not (1 <= birth_month <= 12 and 1 <= birth_day <= 31):
        return {"error": "Invalid date"}
    now = datetime.utcnow().isoformat()
    with get_db() as conn:
        conn.execute(
            """INSERT INTO birthdays
               (id, name, birth_month, birth_day, birth_year, reminder_offsets, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (str(uuid.uuid4()), name, birth_month, birth_day, birth_year, reminder_offsets, now),
        )
    return {"status": "saved"}


def delete_birthday(birthday_id: str) -> dict:
    with get_db() as conn:
        conn.execute("DELETE FROM birthdays WHERE id=?", (birthday_id,))
    return {"status": "deleted"}


def bulk_set_birthday_reminders(ids: list, reminder_offsets) -> dict:
    with get_db() as conn:
        for bid in ids:
            conn.execute(
                "UPDATE birthdays SET reminder_offsets=? WHERE id=?",
                (reminder_offsets, bid),
            )
    return {"status": "saved", "count": len(ids)}


# ── Recurring Manager ──────────────────────────────────────────────────────────

def list_recurring_tasks() -> list:
    with get_db() as conn:
        rows = conn.execute(
            """SELECT text, recur,
                      MIN(section) AS section,
                      COUNT(DISTINCT week_monday) AS week_count,
                      MAX(week_monday) AS latest_week
               FROM tasks
               WHERE recur IS NOT NULL
               GROUP BY text, recur
               ORDER BY recur, text"""
        ).fetchall()
    return [dict(r) for r in rows]


def add_recurring_task(text: str, section: str, recur: str, week_monday: str,
                        day_of_month: int = None) -> dict:
    text = text.strip()
    if not text:
        return {"error": "Text required"}
    if section not in SECTIONS:
        return {"error": "Invalid section"}
    if not recur:
        return {"error": "Recurrence required"}
    n_months = _month_interval(recur)
    # For monthly/annual, anchor created_at to the task's actual calendar date
    if n_months is not None:
        if day_of_month:
            try:
                d = int(day_of_month)
                today = date.today()
                anchor = date(today.year, today.month, min(d, 28))
            except (ValueError, TypeError):
                day_offset = DAY_NAMES.index(section) if section in DAY_NAMES else 0
                anchor = date.fromisoformat(week_monday) + timedelta(days=day_offset)
        else:
            day_offset = DAY_NAMES.index(section) if section in DAY_NAMES else 0
            anchor = date.fromisoformat(week_monday) + timedelta(days=day_offset)
        now = anchor.isoformat() + "T00:00:00"
        origin = anchor
    else:
        now = datetime.utcnow().isoformat()
        origin = None
    with get_db() as conn:
        already = conn.execute(
            "SELECT 1 FROM tasks WHERE week_monday=? AND section=? AND text=?",
            (week_monday, section, text),
        ).fetchone()
        if already:
            return {"error": "Task already exists this week"}
        max_pos = conn.execute(
            "SELECT MAX(position) FROM tasks WHERE week_monday=? AND section=?",
            (week_monday, section),
        ).fetchone()[0]
        pos = (max_pos or 0) + 1
        conn.execute(
            """INSERT INTO tasks
               (id, week_monday, section, position, text, checked,
                recur, attachment, note, created_at)
               VALUES (?,?,?,?,?,0,?,NULL,NULL,?)""",
            (str(uuid.uuid4()), week_monday, section, pos, text, recur, now),
        )
        # Forward-fill next 52 weeks regardless of whether they've been visited
        base = date.fromisoformat(week_monday)
        future_weeks = sorted(
            {r[0] for r in conn.execute(
                "SELECT week_monday FROM weeks_initialized WHERE week_monday > ?",
                (week_monday,),
            ).fetchall()} |
            {(base + timedelta(days=7 * i)).isoformat() for i in range(1, 53)}
        )
        for fw in future_weeks:
            if n_months is not None:
                target_section = _section_for_monthly(origin, n_months, fw)
                if target_section is None:
                    continue
                sections_for_fw = [target_section]
            else:
                sections_for_fw = [s for s in _sections_for_recur(section, recur)
                                   if _should_carry(conn, text, s, recur, fw)]
            for target_section in sections_for_fw:
                fw_already = conn.execute(
                    "SELECT 1 FROM tasks WHERE week_monday=? AND section=? AND text=?",
                    (fw, target_section, text),
                ).fetchone()
                if fw_already:
                    continue
                fw_pos = conn.execute(
                    "SELECT MAX(position) FROM tasks WHERE week_monday=? AND section=?",
                    (fw, target_section),
                ).fetchone()[0]
                conn.execute(
                    """INSERT INTO tasks
                       (id, week_monday, section, position, text, checked,
                        recur, attachment, note, created_at)
                       VALUES (?,?,?,?,?,0,?,NULL,NULL,?)""",
                    (str(uuid.uuid4()), fw, target_section, (fw_pos or 0) + 1,
                     text, recur, now),
                )
    return {"status": "saved"}


def delete_all_recurring(text: str, recur: str) -> dict:
    with get_db() as conn:
        conn.execute(
            "DELETE FROM tasks WHERE text=? AND recur=?",
            (text, recur),
        )
    return {"status": "deleted"}


def import_calendar_csv(csv_text: str) -> dict:
    """Import events from a Google Calendar-compliant CSV export.

    Expected columns (case-insensitive): Subject, Start Date, Start Time,
    End Time, All Day Event, Description, Location. Each row becomes a task
    on the day matching its Start Date; time/location/description are
    folded into the task's note since Athena tasks have no time field.
    """
    reader = csv.DictReader(io.StringIO(csv_text))
    if not reader.fieldnames:
        return {"error": "Empty or invalid CSV"}

    field_lookup = {f.strip().lower(): f for f in reader.fieldnames if f}

    def get(row, name):
        key = field_lookup.get(name.lower())
        if not key:
            return ""
        val = row.get(key)
        return val.strip() if val else ""

    # Re-importing should fully replace upcoming imported events — otherwise
    # events deleted on the source calendar keep reappearing in Athena since
    # import only ever adds rows and nothing else ever removes them.
    # Past imported events are left alone so history isn't wiped.
    today = date.today()
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, week_monday, section FROM tasks WHERE source='csv_import'"
        ).fetchall()
        for r in rows:
            if r["section"] not in DAY_NAMES:
                continue
            day_offset = DAY_NAMES.index(r["section"])
            event_date = date.fromisoformat(r["week_monday"]) + timedelta(days=day_offset)
            if event_date >= today:
                conn.execute("DELETE FROM tasks WHERE id=?", (r["id"],))
        for wm, section in {(r["week_monday"], r["section"]) for r in rows}:
            _normalize_positions(conn, wm, section)

    imported = 0
    skipped = 0
    errors = []

    for i, row in enumerate(reader, start=2):
        subject = get(row, "Subject")
        start_date_raw = get(row, "Start Date")
        if not subject or not start_date_raw:
            skipped += 1
            continue

        event_date = None
        for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
            try:
                event_date = datetime.strptime(start_date_raw, fmt).date()
                break
            except ValueError:
                continue
        if event_date is None:
            errors.append(f"Row {i}: unrecognized date '{start_date_raw}'")
            skipped += 1
            continue

        all_day = get(row, "All Day Event").lower() in ("true", "1", "yes")
        start_time = get(row, "Start Time")
        end_time = get(row, "End Time")
        location = get(row, "Location")
        description = get(row, "Description")

        # Unquoted commas inside the last column spill into DictReader's
        # restkey (None) as extra fields instead of staying part of it.
        overflow = row.get(None)
        if overflow:
            extra = ",".join(v for v in overflow if v)
            description = f"{description},{extra}" if description else extra

        note_parts = []
        if not all_day and start_time:
            note_parts.append(f"{start_time} - {end_time}" if end_time else start_time)
        if location:
            note_parts.append(f"Location: {location}")
        if description:
            note_parts.append(description)
        note = "\n".join(note_parts) if note_parts else None

        section = DAY_NAMES[event_date.weekday()]
        result = add_task(event_date.isoformat(), section, subject, recur=None,
                           note=note, source='csv_import')
        if "error" in result:
            errors.append(f"Row {i}: {result['error']}")
            skipped += 1
        else:
            imported += 1

    return {"status": "done", "imported": imported, "skipped": skipped, "errors": errors[:20]}


def list_imported_tasks() -> list:
    """Flat list of CSV-imported events, one per line, ordered by date."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, text, week_monday, section, note FROM tasks WHERE source='csv_import'"
        ).fetchall()
    items = []
    for r in rows:
        day_offset = DAY_NAMES.index(r["section"]) if r["section"] in DAY_NAMES else 0
        event_date = date.fromisoformat(r["week_monday"]) + timedelta(days=day_offset)
        items.append({
            "id": r["id"],
            "text": r["text"],
            "date": event_date.isoformat(),
            "section": r["section"],
            "note": r["note"],
        })
    items.sort(key=lambda t: t["date"])
    return items


def delete_imported_task(task_id: str) -> dict:
    with get_db() as conn:
        row = conn.execute(
            "SELECT week_monday, section FROM tasks WHERE id=? AND source='csv_import'",
            (task_id,),
        ).fetchone()
        if not row:
            return {"error": "Task not found"}
        conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))
        _normalize_positions(conn, row["week_monday"], row["section"])
    return {"status": "deleted"}


