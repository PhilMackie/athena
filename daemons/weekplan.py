"""WeekPlanDaemon - Weekly planner with SQLite storage.

Source of truth: data/athena.db
Recurring tasks: vault/Projects/Quanta/Templates/WeeklyRecurring.md
"""

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
                completed_at TEXT
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_tasks_week_section
            ON tasks (week_monday, section, position)
        """)


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
    tasks = conn.execute(
        "SELECT * FROM tasks WHERE week_monday=? AND section=? ORDER BY position",
        (week_monday, section),
    ).fetchall()
    if section_idx < 0 or section_idx >= len(tasks):
        return None
    return tasks[section_idx]


def _normalize_positions(conn, week_monday: str, section: str):
    rows = conn.execute(
        "SELECT id FROM tasks WHERE week_monday=? AND section=? ORDER BY position",
        (week_monday, section),
    ).fetchall()
    for i, row in enumerate(rows):
        conn.execute("UPDATE tasks SET position=? WHERE id=?", (i, row["id"]))


def _row_to_task(row, section_idx: int) -> dict:
    return {
        "text":        row["text"],
        "checked":     bool(row["checked"]),
        "section_idx": section_idx,
        "recur":       row["recur"],
        "attachment":  row["attachment"],
        "note":        row["note"],
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

def get_or_create_week(date_str: str) -> dict:
    monday, sunday = get_week_bounds(date_str)
    week_monday = monday.isoformat()

    with get_db() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE week_monday=?", (week_monday,)
        ).fetchone()[0]

        if count == 0:
            prev_monday = (monday - timedelta(days=7)).isoformat()
            carry = conn.execute(
                """SELECT * FROM tasks WHERE week_monday=? AND checked=0
                   AND (recur IS NOT NULL OR section='Someday')
                   ORDER BY section, position""",
                (prev_monday,),
            ).fetchall()
            now = datetime.utcnow().isoformat()
            for t in carry:
                conn.execute(
                    """INSERT INTO tasks
                       (id, week_monday, section, position, text, checked,
                        recur, attachment, note, created_at)
                       VALUES (?,?,?,?,?,0,?,?,?,?)""",
                    (str(uuid.uuid4()), week_monday, t["section"], t["position"],
                     t["text"], t["recur"], t["attachment"], t["note"], now),
                )
            for section in SECTIONS:
                _normalize_positions(conn, week_monday, section)

        rows = conn.execute(
            "SELECT * FROM tasks WHERE week_monday=? ORDER BY section, position",
            (week_monday,),
        ).fetchall()

    by_section = {s: [] for s in SECTIONS}
    for row in rows:
        if row["section"] in by_section:
            by_section[row["section"]].append(row)

    indexed = {
        s: [_row_to_task(r, i) for i, r in enumerate(by_section[s])]
        for s in SECTIONS
    }

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
            "tasks":    indexed[name],
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
        "someday":   indexed["Someday"],
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
    week_monday = _get_monday(date_str)
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
    return {"status": "saved"}


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


def set_task_recur(date_str: str, section: str, section_idx: int, recur: str) -> dict:
    if section not in SECTIONS:
        return {"error": "Invalid section"}
    week_monday = _get_monday(date_str)
    with get_db() as conn:
        task = _get_task_by_idx(conn, week_monday, section, section_idx)
        if not task:
            return {"error": "Task not found"}
        conn.execute("UPDATE tasks SET recur=? WHERE id=?",
                     (recur.strip() or None, task["id"]))
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
            target_monday, target_section = week_monday, "Someday"

        elif defer_to == "tomorrow":
            if section == "Someday":
                return {"error": "Cannot defer Someday task to tomorrow"}
            tomorrow = date.fromisoformat(date_str) + timedelta(days=1)
            target_monday = _get_monday(tomorrow.isoformat())
            target_section = tomorrow.strftime("%A")

        elif defer_to == "next_week":
            if section == "Someday":
                next_mon = date.fromisoformat(week_monday) + timedelta(days=7)
                target_monday, target_section = next_mon.isoformat(), "Someday"
            else:
                next_day = date.fromisoformat(date_str) + timedelta(days=7)
                target_monday = _get_monday(next_day.isoformat())
                target_section = next_day.strftime("%A")
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


def duplicate_task(date_str: str, section: str, section_idx: int) -> dict:
    if section not in SECTIONS:
        return {"error": "Invalid section"}
    week_monday = _get_monday(date_str)
    with get_db() as conn:
        task = _get_task_by_idx(conn, week_monday, section, section_idx)
        if not task:
            return {"error": "Task not found"}
        conn.execute(
            "UPDATE tasks SET position=position+1 WHERE week_monday=? AND section=? AND position>?",
            (week_monday, section, task["position"]),
        )
        now = datetime.utcnow().isoformat()
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


def reorder_section(date_str: str, section: str, ordered_texts: list) -> dict:
    if section not in SECTIONS:
        return {"error": "Invalid section"}
    week_monday = _get_monday(date_str)
    with get_db() as conn:
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
