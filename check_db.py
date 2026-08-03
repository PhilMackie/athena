import sys
sys.path.insert(0, '/opt/athena/app')
from daemons.weekplan import get_db
with get_db() as conn:
    migrations = [r[0] for r in conn.execute('SELECT name FROM migrations ORDER BY name').fetchall()]
    print("MIGRATIONS:", migrations)
    weeks = conn.execute('SELECT week_monday, COUNT(*) FROM tasks WHERE recur IS NOT NULL GROUP BY week_monday ORDER BY week_monday').fetchall()
    print("RECURRING BY WEEK:")
    for w in weeks:
        print(f"  {w[0]}: {w[1]} tasks")
