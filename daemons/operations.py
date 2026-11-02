"""OperationsDaemon — named, date-ranged "Operation" focus modes.

An Operation is a named campaign pinned to a contiguous, inclusive date range
(start_date .. end_date) and can therefore span several days and cross week
boundaries. While an Operation covers a day column, that column goes into
tunnel vision: the global WeeklyRecurring ghosts, every task carrying a
`recur` cadence, and every untagged one-off are hidden, leaving only the tasks
tagged into the Operation via `tasks.operation_id`.

Nothing is destroyed to achieve that. The week payload still carries every
task; hiding is a render-time filter in the UI, so ending or deleting an
Operation brings the ordinary week straight back. `operations` has no FK onto
`tasks` — `delete_operation()` clears `operation_id` by hand, the same way
`delete_block()` handles `tasks.block_id`.

An Operation also owns a flat "mission objectives" checklist (`operation_tasks`)
— date-independent, unlike everything else in this app. Where a day task is
tied to a specific date and a block task floats free of the calendar entirely,
an objective belongs to the *Operation itself*: it exists from launch to
deletion regardless of which week is on screen or whether today falls inside
the range. This is the one list in Athena with a real FK + CASCADE (mirroring
`time_block_tasks` -> `time_blocks`) rather than a manually-cleared tag,
because an objective has no meaning once its Operation is gone.
"""

import uuid
from datetime import date, datetime, timedelta

from daemons.weekplan import get_db

MAX_OPERATION_DAYS = 366


def date_range(start_date: str, end_date: str) -> list:
    """Every ISO date the inclusive range covers."""
    s = date.fromisoformat(start_date)
    e = date.fromisoformat(end_date)
    return [(s + timedelta(days=i)).isoformat() for i in range((e - s).days + 1)]


def init_operations_db():
    """Create the operations table if it doesn't exist. Called at app startup."""
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS operations (
                id         TEXT PRIMARY KEY,
                name       TEXT NOT NULL,
                start_date TEXT NOT NULL,
                end_date   TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_operations_range
            ON operations (start_date, end_date)
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS operation_tasks (
                id           TEXT PRIMARY KEY,
                operation_id TEXT NOT NULL REFERENCES operations(id) ON DELETE CASCADE,
                position     INTEGER NOT NULL,
                text         TEXT NOT NULL,
                checked      INTEGER NOT NULL DEFAULT 0,
                created_at   TEXT NOT NULL,
                completed_at TEXT
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_operation_tasks_op
            ON operation_tasks (operation_id, position)
        """)


def _clean_dates(start_date: str, end_date: str):
    """Normalize a range to (start, end, error). Reversed ranges are swapped."""
    try:
        s = date.fromisoformat((start_date or "").strip())
        e = date.fromisoformat((end_date or "").strip())
    except ValueError:
        return None, None, "Dates must be YYYY-MM-DD"
    if e < s:
        s, e = e, s
    if (e - s).days + 1 > MAX_OPERATION_DAYS:
        return None, None, f"An Operation cannot run longer than {MAX_OPERATION_DAYS} days"
    return s.isoformat(), e.isoformat(), None


def _row_to_objective(row) -> dict:
    return {
        "id":       row["id"],
        "text":     row["text"],
        "checked":  bool(row["checked"]),
        "position": row["position"],
    }


def _row_to_operation(row, task_count: int = 0, objectives: list = None) -> dict:
    dates = date_range(row["start_date"], row["end_date"])
    today = date.today().isoformat()
    objectives = objectives or []
    return {
        "id":          row["id"],
        "name":        row["name"],
        "start_date":  row["start_date"],
        "end_date":    row["end_date"],
        "dates":       dates,
        "day_count":   len(dates),
        "today_index": dates.index(today) + 1 if today in dates else None,
        "status":      ("active" if row["start_date"] <= today <= row["end_date"]
                        else "upcoming" if today < row["start_date"] else "past"),
        "task_count":  task_count,
        "objectives":  objectives,
        "objectives_done":  sum(1 for o in objectives if o["checked"]),
        "objectives_total": len(objectives),
        "created_at":  row["created_at"],
    }


# ── Operations ──────────────────────────────────────────────────────────────

def list_operations() -> list:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM operations ORDER BY start_date DESC, created_at DESC"
        ).fetchall()
        counts = dict(conn.execute(
            "SELECT operation_id, COUNT(*) FROM tasks"
            " WHERE operation_id IS NOT NULL GROUP BY operation_id"
        ).fetchall())
        obj_rows = conn.execute(
            "SELECT * FROM operation_tasks ORDER BY operation_id, position"
        ).fetchall()
    objectives_by_op = {}
    for r in obj_rows:
        objectives_by_op.setdefault(r["operation_id"], []).append(_row_to_objective(r))
    return [_row_to_operation(r, counts.get(r["id"], 0), objectives_by_op.get(r["id"], []))
            for r in rows]


def add_operation(name: str, start_date: str, end_date: str) -> dict:
    name = (name or "").strip()
    if not name:
        return {"error": "Operation name required"}
    start, end, err = _clean_dates(start_date, end_date)
    if err:
        return {"error": err}
    op_id = str(uuid.uuid4())
    with get_db() as conn:
        conn.execute(
            "INSERT INTO operations VALUES (?,?,?,?,?)",
            (op_id, name, start, end, datetime.utcnow().isoformat()),
        )
    return {"status": "saved", "id": op_id}


def rename_operation(operation_id: str, name: str) -> dict:
    name = (name or "").strip()
    if not name:
        return {"error": "Operation name required"}
    with get_db() as conn:
        cur = conn.execute(
            "UPDATE operations SET name=? WHERE id=?", (name, operation_id)
        )
        if cur.rowcount == 0:
            return {"error": "Operation not found"}
    return {"status": "saved"}


def set_operation_dates(operation_id: str, start_date: str, end_date: str) -> dict:
    """Re-range an Operation. Tags on days the new range no longer covers are
    cleared — an operation_id only means anything on a covered date."""
    start, end, err = _clean_dates(start_date, end_date)
    if err:
        return {"error": err}
    with get_db() as conn:
        cur = conn.execute(
            "UPDATE operations SET start_date=?, end_date=? WHERE id=?",
            (start, end, operation_id),
        )
        if cur.rowcount == 0:
            return {"error": "Operation not found"}
        _clear_tags_outside_range(conn, operation_id, start, end)
    return {"status": "saved"}


def delete_operation(operation_id: str) -> dict:
    """Delete an Operation and untag its tasks. The tasks themselves survive —
    they simply stop being Operation work and reappear in the normal week."""
    with get_db() as conn:
        conn.execute(
            "UPDATE tasks SET operation_id=NULL WHERE operation_id=?", (operation_id,)
        )
        cur = conn.execute("DELETE FROM operations WHERE id=?", (operation_id,))
        if cur.rowcount == 0:
            return {"error": "Operation not found"}
    return {"status": "deleted"}


def _clear_tags_outside_range(conn, operation_id: str, start: str, end: str):
    """Untag any task tagged to this Operation that no longer sits on a covered
    date. Day tasks derive their date from week_monday + section offset;
    Scratchpad/Someday rows have no date at all, so they can never be covered."""
    from daemons.weekplan import DAY_NAMES

    rows = conn.execute(
        "SELECT id, week_monday, section FROM tasks WHERE operation_id=?",
        (operation_id,),
    ).fetchall()
    for row in rows:
        if row["section"] not in DAY_NAMES:
            conn.execute("UPDATE tasks SET operation_id=NULL WHERE id=?", (row["id"],))
            continue
        task_date = (date.fromisoformat(row["week_monday"])
                     + timedelta(days=DAY_NAMES.index(row["section"]))).isoformat()
        if not (start <= task_date <= end):
            conn.execute("UPDATE tasks SET operation_id=NULL WHERE id=?", (row["id"],))


# ── Mission objectives ────────────────────────────────────────────────────────
# A flat checklist owned by the Operation itself — no date, no section, no day
# column. It exists for as long as the Operation does, independent of which
# week is on screen or whether the Operation's range has started, is current,
# or is over.

def _normalize_objective_positions(conn, operation_id: str):
    rows = conn.execute(
        "SELECT id FROM operation_tasks WHERE operation_id=? ORDER BY position",
        (operation_id,),
    ).fetchall()
    for i, row in enumerate(rows):
        conn.execute("UPDATE operation_tasks SET position=? WHERE id=?", (i, row["id"]))


def list_objectives(operation_id: str) -> list:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM operation_tasks WHERE operation_id=? ORDER BY position",
            (operation_id,),
        ).fetchall()
    return [_row_to_objective(r) for r in rows]


def add_objective(operation_id: str, text: str) -> dict:
    text = (text or "").strip()
    if not text:
        return {"error": "Objective text required"}
    now = datetime.utcnow().isoformat()
    with get_db() as conn:
        if not conn.execute("SELECT 1 FROM operations WHERE id=?", (operation_id,)).fetchone():
            return {"error": "Operation not found"}
        max_pos = conn.execute(
            "SELECT COALESCE(MAX(position), -1) FROM operation_tasks WHERE operation_id=?",
            (operation_id,),
        ).fetchone()[0]
        task_id = str(uuid.uuid4())
        conn.execute(
            """INSERT INTO operation_tasks
               (id, operation_id, position, text, checked, created_at)
               VALUES (?,?,?,?,0,?)""",
            (task_id, operation_id, max_pos + 1, text, now),
        )
    return {"id": task_id, "status": "saved"}


def toggle_objective(operation_id: str, objective_id: str, checked: bool) -> dict:
    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM operation_tasks WHERE id=? AND operation_id=?",
            (objective_id, operation_id),
        ).fetchone()
        if not row:
            return {"error": "Objective not found"}
        completed_at = datetime.utcnow().isoformat() if checked else None
        conn.execute(
            "UPDATE operation_tasks SET checked=?, completed_at=? WHERE id=?",
            (1 if checked else 0, completed_at, objective_id),
        )
    return {"status": "saved"}


def rename_objective(operation_id: str, objective_id: str, text: str) -> dict:
    text = (text or "").strip()
    if not text:
        return {"error": "Objective text required"}
    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM operation_tasks WHERE id=? AND operation_id=?",
            (objective_id, operation_id),
        ).fetchone()
        if not row:
            return {"error": "Objective not found"}
        conn.execute("UPDATE operation_tasks SET text=? WHERE id=?", (text, objective_id))
    return {"status": "saved"}


def delete_objective(operation_id: str, objective_id: str) -> dict:
    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM operation_tasks WHERE id=? AND operation_id=?",
            (objective_id, operation_id),
        ).fetchone()
        if not row:
            return {"error": "Objective not found"}
        conn.execute("DELETE FROM operation_tasks WHERE id=?", (objective_id,))
        _normalize_objective_positions(conn, operation_id)
    return {"status": "saved"}


def reorder_objectives(operation_id: str, ordered_ids: list) -> dict:
    with get_db() as conn:
        for i, objective_id in enumerate(ordered_ids):
            conn.execute(
                "UPDATE operation_tasks SET position=? WHERE id=? AND operation_id=?",
                (i, objective_id, operation_id),
            )
        _normalize_objective_positions(conn, operation_id)
    return {"status": "saved"}
