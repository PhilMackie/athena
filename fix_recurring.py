import sys, uuid, sqlite3, re
from datetime import date, timedelta, datetime
from pathlib import Path
from contextlib import contextmanager

DB_PATH = Path('/opt/athena/data/athena.db')

@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

DAY_NAMES = ['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday']
_WEEKDAYS = ['Monday','Tuesday','Wednesday','Thursday','Friday']
_DAY_ABBR = {'Sun':'Sunday','Mon':'Monday','Tue':'Tuesday','Wed':'Wednesday',
             'Thu':'Thursday','Fri':'Friday','Sat':'Saturday'}

def _sections_for_recur(section, recur):
    if not recur or section == 'Someday':
        return [section]
    if recur == 'daily':
        return DAY_NAMES[:]
    if recur == 'weekdays':
        return _WEEKDAYS[:]
    m = re.match(r'^every (Sun|Mon|Tue|Wed|Thu|Fri|Sat)$', recur, re.IGNORECASE)
    if m:
        return [_DAY_ABBR.get(m.group(1).capitalize(), section)]
    return [section]

def _should_carry(conn, text, section, recur, target_monday):
    return True  # for a one-shot fix, just inject everything

today = date.today()
current_monday = today - timedelta(days=today.weekday())
target_weeks = [(current_monday + timedelta(days=7*i)).isoformat() for i in range(1, 9)]

with get_db() as conn:
    # Find all recurring tasks (any instance)
    all_recur = conn.execute(
        "SELECT * FROM tasks WHERE recur IS NOT NULL ORDER BY week_monday DESC"
    ).fetchall()

    seen = {}
    for row in all_recur:
        key = (row['text'], row['section'], row['recur'])
        if key not in seen:
            seen[key] = row

    now = datetime.utcnow().isoformat()
    inserted = 0
    for (text, section, recur), task in seen.items():
        for fw in target_weeks:
            for target_section in _sections_for_recur(section, recur):
                if not _should_carry(conn, text, target_section, recur, fw):
                    continue
                already = conn.execute(
                    "SELECT 1 FROM tasks WHERE week_monday=? AND section=? AND text=?",
                    (fw, target_section, text)
                ).fetchone()
                if already:
                    continue
                pos = conn.execute(
                    "SELECT COALESCE(MAX(position),-1)+1 FROM tasks WHERE week_monday=? AND section=?",
                    (fw, target_section)
                ).fetchone()[0]
                conn.execute(
                    """INSERT INTO tasks (id,week_monday,section,position,text,checked,recur,attachment,note,created_at,nra_binding,dwm_binding,steps,color)
                       VALUES (?,?,?,?,?,0,?,?,?,?,?,?,?,?)""",
                    (str(uuid.uuid4()), fw, target_section, pos, text, recur,
                     task['attachment'], task['note'], now,
                     task['nra_binding'], task['dwm_binding'], task['steps'], task['color'])
                )
                inserted += 1
                print(f"  + {fw} / {target_section}: {text} ({recur})")

    print(f"\nDone. {inserted} tasks inserted across {len(target_weeks)} weeks.")
