"""BlocksDaemon - Focused "Time Block" task buckets, separate from the weekly grid.

Blocks are global (not week-scoped) renameable containers. A block can hold two
kinds of tasks:
  - "own" tasks: created directly in the block, live only in time_block_tasks.
  - "linked" tasks: real day/Scratchpad/Someday rows (from daemons.weekplan's
    tasks table) tagged with this block's id via tasks.block_id. A linked task
    stays exactly where it is on the grid AND also shows up in the block —
    it's the same task in two views, not a copy, so checking/renaming/editing
    it anywhere updates it everywhere.
"""

import json
import uuid
from datetime import date, datetime, timedelta

from daemons.weekplan import (
    get_db, add_task, set_task_color, set_task_block,
    DAY_NAMES, SOMEDAY_WEEK, _get_monday,
)


def _task_date_str(week_monday: str, section: str) -> str:
    """The specific calendar date for a tasks row, given its week_monday + section."""
    if section in DAY_NAMES:
        d = date.fromisoformat(week_monday) + timedelta(days=DAY_NAMES.index(section))
        return d.isoformat()
    return week_monday  # Scratchpad/Someday: week_monday is all the API needs


def _find_section_idx(conn, week_monday: str, section: str, task_id: str):
    rows = conn.execute(
        "SELECT id FROM tasks WHERE week_monday=? AND section=? ORDER BY position",
        (week_monday, section),
    ).fetchall()
    for i, r in enumerate(rows):
        if r["id"] == task_id:
            return i
    return None


def init_blocks_db():
    """Create block tables if they don't exist, and seed 2 default blocks once."""
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS time_blocks (
                id         TEXT PRIMARY KEY,
                name       TEXT NOT NULL,
                position   INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS time_block_tasks (
                id           TEXT PRIMARY KEY,
                block_id     TEXT NOT NULL REFERENCES time_blocks(id) ON DELETE CASCADE,
                position     INTEGER NOT NULL,
                text         TEXT NOT NULL,
                checked      INTEGER NOT NULL DEFAULT 0,
                note         TEXT,
                color        TEXT,
                created_at   TEXT NOT NULL,
                completed_at TEXT
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_time_block_tasks_block
            ON time_block_tasks (block_id, position)
        """)
        if not conn.execute("SELECT 1 FROM migrations WHERE name='seed_default_blocks'").fetchone():
            now = datetime.utcnow().isoformat()
            conn.execute("INSERT INTO time_blocks VALUES (?,?,?,?)", (str(uuid.uuid4()), "Block 1", 0, now))
            conn.execute("INSERT INTO time_blocks VALUES (?,?,?,?)", (str(uuid.uuid4()), "Block 2", 1, now))
            conn.execute("INSERT INTO migrations VALUES ('seed_default_blocks')")


# ── Blocks ──────────────────────────────────────────────────────────────────

def list_blocks() -> list:
    with get_db() as conn:
        blocks = conn.execute("SELECT * FROM time_blocks ORDER BY position").fetchall()
        result = []
        for b in blocks:
            own_tasks = conn.execute(
                "SELECT * FROM time_block_tasks WHERE block_id=? ORDER BY position",
                (b["id"],),
            ).fetchall()
            linked_rows = conn.execute(
                "SELECT * FROM tasks WHERE block_id=? ORDER BY created_at",
                (b["id"],),
            ).fetchall()
            linked_tasks = []
            for row in linked_rows:
                idx = _find_section_idx(conn, row["week_monday"], row["section"], row["id"])
                if idx is None:
                    continue  # stale tag on a row that's since vanished; skip defensively
                linked_tasks.append({
                    "kind": "linked",
                    "id": row["id"],
                    "block_id": b["id"],
                    "text": row["text"],
                    "checked": bool(row["checked"]),
                    "note": row["note"],
                    "color": row["color"],
                    "recur": row["recur"],
                    "attachment": row["attachment"],
                    "steps": json.loads(row["steps"]) if row["steps"] else [],
                    "date_str": _task_date_str(row["week_monday"], row["section"]),
                    "section": row["section"],
                    "section_idx": idx,
                })
            result.append({
                "id": b["id"],
                "name": b["name"],
                "position": b["position"],
                "tasks": [
                    {
                        "kind": "own",
                        "id": t["id"],
                        "text": t["text"],
                        "checked": bool(t["checked"]),
                        "note": t["note"],
                        "color": t["color"],
                        "position": t["position"],
                    }
                    for t in own_tasks
                ],
                "linked_tasks": linked_tasks,
            })
    return result


def add_block(name: str = None) -> dict:
    now = datetime.utcnow().isoformat()
    with get_db() as conn:
        max_pos = conn.execute("SELECT COALESCE(MAX(position), -1) FROM time_blocks").fetchone()[0]
        count = conn.execute("SELECT COUNT(*) FROM time_blocks").fetchone()[0]
        block_name = (name or "").strip() or f"Block {count + 1}"
        block_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO time_blocks VALUES (?,?,?,?)",
            (block_id, block_name, max_pos + 1, now),
        )
    return {"id": block_id, "name": block_name, "position": max_pos + 1}


def rename_block(block_id: str, name: str) -> dict:
    name = (name or "").strip()
    if not name:
        return {"error": "Name required"}
    with get_db() as conn:
        row = conn.execute("SELECT 1 FROM time_blocks WHERE id=?", (block_id,)).fetchone()
        if not row:
            return {"error": "Block not found"}
        conn.execute("UPDATE time_blocks SET name=? WHERE id=?", (name, block_id))
    return {"status": "saved"}


def delete_block(block_id: str) -> dict:
    with get_db() as conn:
        # tasks.block_id has no FK/cascade (it's a plain column on a table
        # blocks.py doesn't own) — clear it explicitly or linked day tasks
        # would keep pointing at a now-deleted block.
        conn.execute("UPDATE tasks SET block_id=NULL WHERE block_id=?", (block_id,))
        conn.execute("DELETE FROM time_blocks WHERE id=?", (block_id,))
    return {"status": "deleted"}


# ── Block tasks ───────────────────────────────────────────────────────────────

def _normalize_block_positions(conn, block_id: str):
    tasks = conn.execute(
        "SELECT id FROM time_block_tasks WHERE block_id=? ORDER BY position",
        (block_id,),
    ).fetchall()
    for i, row in enumerate(tasks):
        conn.execute("UPDATE time_block_tasks SET position=? WHERE id=?", (i, row["id"]))


def add_block_task(block_id: str, text: str, note: str = None, color: str = None) -> dict:
    text = (text or "").strip()
    if not text:
        return {"error": "Task text required"}
    now = datetime.utcnow().isoformat()
    with get_db() as conn:
        if not conn.execute("SELECT 1 FROM time_blocks WHERE id=?", (block_id,)).fetchone():
            return {"error": "Block not found"}
        max_pos = conn.execute(
            "SELECT COALESCE(MAX(position), -1) FROM time_block_tasks WHERE block_id=?",
            (block_id,),
        ).fetchone()[0]
        task_id = str(uuid.uuid4())
        conn.execute(
            """INSERT INTO time_block_tasks
               (id, block_id, position, text, checked, note, color, created_at)
               VALUES (?,?,?,?,0,?,?,?)""",
            (task_id, block_id, max_pos + 1, text, note.strip() if note else None, color or None, now),
        )
    return {"id": task_id, "status": "saved"}


def toggle_block_task(block_id: str, task_id: str, checked: bool) -> dict:
    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM time_block_tasks WHERE id=? AND block_id=?", (task_id, block_id)
        ).fetchone()
        if not row:
            return {"error": "Task not found"}
        completed_at = datetime.utcnow().isoformat() if checked else None
        conn.execute(
            "UPDATE time_block_tasks SET checked=?, completed_at=? WHERE id=?",
            (1 if checked else 0, completed_at, task_id),
        )
    return {"status": "saved"}


def rename_block_task(block_id: str, task_id: str, text: str) -> dict:
    text = (text or "").strip()
    if not text:
        return {"error": "Task text required"}
    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM time_block_tasks WHERE id=? AND block_id=?", (task_id, block_id)
        ).fetchone()
        if not row:
            return {"error": "Task not found"}
        conn.execute("UPDATE time_block_tasks SET text=? WHERE id=?", (text, task_id))
    return {"status": "saved"}


def set_block_task_note(block_id: str, task_id: str, note: str) -> dict:
    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM time_block_tasks WHERE id=? AND block_id=?", (task_id, block_id)
        ).fetchone()
        if not row:
            return {"error": "Task not found"}
        val = note.strip() if note else None
        conn.execute("UPDATE time_block_tasks SET note=? WHERE id=?", (val or None, task_id))
    return {"status": "saved"}


def set_block_task_color(block_id: str, task_id: str, color: str) -> dict:
    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM time_block_tasks WHERE id=? AND block_id=?", (task_id, block_id)
        ).fetchone()
        if not row:
            return {"error": "Task not found"}
        val = (color or "").strip() or None
        conn.execute("UPDATE time_block_tasks SET color=? WHERE id=?", (val, task_id))
    return {"status": "saved"}


def delete_block_task(block_id: str, task_id: str) -> dict:
    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM time_block_tasks WHERE id=? AND block_id=?", (task_id, block_id)
        ).fetchone()
        if not row:
            return {"error": "Task not found"}
        conn.execute("DELETE FROM time_block_tasks WHERE id=?", (task_id,))
        _normalize_block_positions(conn, block_id)
    return {"status": "saved"}


def reorder_block_tasks(block_id: str, ordered_ids: list) -> dict:
    with get_db() as conn:
        for i, task_id in enumerate(ordered_ids):
            conn.execute(
                "UPDATE time_block_tasks SET position=? WHERE id=? AND block_id=?",
                (i, task_id, block_id),
            )
    return {"status": "saved"}


def move_block_task(from_block_id: str, task_id: str, to_block_id: str) -> dict:
    with get_db() as conn:
        task = conn.execute(
            "SELECT * FROM time_block_tasks WHERE id=? AND block_id=?", (task_id, from_block_id)
        ).fetchone()
        if not task:
            return {"error": "Task not found"}
        if not conn.execute("SELECT 1 FROM time_blocks WHERE id=?", (to_block_id,)).fetchone():
            return {"error": "Destination block not found"}
        max_pos = conn.execute(
            "SELECT COALESCE(MAX(position), -1) FROM time_block_tasks WHERE block_id=?",
            (to_block_id,),
        ).fetchone()[0]
        conn.execute(
            "UPDATE time_block_tasks SET block_id=?, position=? WHERE id=?",
            (to_block_id, max_pos + 1, task_id),
        )
        _normalize_block_positions(conn, from_block_id)
    return {"status": "saved"}


# ── Linking day tasks to blocks ───────────────────────────────────────────────
# A linked task stays on its day/Scratchpad/Someday row — these just set/clear
# tasks.block_id so the same row also surfaces inside a block.

def link_task_to_block(date_str: str, section: str, section_idx: int, block_id: str) -> dict:
    """Tag a day/Scratchpad/Someday task so it also shows up in `block_id`.
    The task is NOT removed from its day — it now appears in both places."""
    if not block_id:
        return {"error": "block_id required"}
    with get_db() as conn:
        if not conn.execute("SELECT 1 FROM time_blocks WHERE id=?", (block_id,)).fetchone():
            return {"error": "Block not found"}
    return set_task_block(date_str, section, section_idx, block_id)


def unlink_task_from_block(date_str: str, section: str, section_idx: int) -> dict:
    """Remove a linked task from whichever block it's tagged with. The task
    itself is untouched and keeps living on its day/Scratchpad/Someday row."""
    return set_task_block(date_str, section, section_idx, None)


def send_block_task_to_day(block_id: str, task_id: str, date_str: str, section: str) -> dict:
    """Turn an "own" block-only task into a real day/Scratchpad/Someday task,
    while keeping it tagged to this block — it now shows in both places,
    same as linking an existing day task would."""
    with get_db() as conn:
        task = conn.execute(
            "SELECT * FROM time_block_tasks WHERE id=? AND block_id=?", (task_id, block_id)
        ).fetchone()
        if not task:
            return {"error": "Task not found"}
        text, note, color = task["text"], task["note"], task["color"]
        conn.execute("DELETE FROM time_block_tasks WHERE id=?", (task_id,))
        _normalize_block_positions(conn, block_id)
    result = add_task(date_str, section, text, note=note)
    if "error" in result:
        return result
    week_monday = SOMEDAY_WEEK if section == 'Someday' else _get_monday(date_str)
    with get_db() as conn:
        # add_task() appends the new row last in position order, so its
        # section_idx (array index, not raw `position`) is count-1 —
        # positions aren't guaranteed contiguous (recurring forward-fill
        # can leave gaps), so MAX(position) is not a valid section_idx.
        count = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE week_monday=? AND section=?",
            (week_monday, section),
        ).fetchone()[0]
    section_idx = count - 1
    set_task_block(date_str, section, section_idx, block_id)
    if color:
        set_task_color(date_str, section, section_idx, color)
    return {"status": "saved"}
