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


def _row_to_operation(row, task_count: int = 0) -> dict:
    dates = date_range(row["start_date"], row["end_date"])
    today = date.today().isoformat()
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
    return [_row_to_operation(r, counts.get(r["id"], 0)) for r in rows]


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
