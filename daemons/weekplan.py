"""WeekPlanDaemon - Tweek-inspired weekly planner.

Storage: vault/Journals/Weekly/YYYY-WXX.md
Recurring tasks: vault/Projects/Quanta/Templates/WeeklyRecurring.md
"""

import re
from datetime import date, timedelta
from pathlib import Path
import config

WEEKPLAN_DIR = config.OBSIDIAN_VAULT / "Journals" / "Weekly"
RECURRING_FILE = config.TEMPLATES_DIR / "WeeklyRecurring.md"

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
SECTIONS = DAY_NAMES + ["Someday"]
MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def get_week_bounds(date_str: str) -> tuple:
    """Return (monday, sunday) dates for the week containing date_str."""
    d = date.fromisoformat(date_str)
    monday = d - timedelta(days=d.weekday())
    sunday = monday + timedelta(days=6)
    return monday, sunday


def get_week_path(date_str: str) -> Path:
    """Return path to YYYY-WXX.md for the week containing date_str."""
    monday, _ = get_week_bounds(date_str)
    iso = monday.isocalendar()
    year, week_num = iso[0], iso[1]
    WEEKPLAN_DIR.mkdir(parents=True, exist_ok=True)
    return WEEKPLAN_DIR / f"{year}-W{week_num:02d}.md"


def _parse_task_line(line: str, line_num: int) -> dict | None:
    """Parse a markdown task line. Returns None if not a task."""
    m = re.match(r'^- \[([ xX])\] (.+)$', line)
    if not m:
        return None

    checked = m.group(1).lower() == 'x'
    raw_text = m.group(2)

    recur_m = re.search(r'\[recur:([^\]]+)\]', raw_text)
    attach_m = re.search(r'\[attach:([^\]]+)\]', raw_text)

    recur = recur_m.group(1) if recur_m else None
    attachment = attach_m.group(1) if attach_m else None

    # Strip metadata tags to get clean display text
    clean = re.sub(r'\s*\[(?:recur|attach):[^\]]+\]', '', raw_text).strip()

    return {
        "text": clean,
        "checked": checked,
        "line_num": line_num,
        "recur": recur,
        "attachment": attachment,
        "note": None,
    }


def _parse_file(path: Path) -> dict:
    """Parse week file into {week_label, sections: {day: [tasks]}}."""
    sections = {s: [] for s in SECTIONS}
    week_label = ""

    if not path.exists():
        return {"week_label": week_label, "sections": sections}

    content = path.read_text(encoding="utf-8")
    lines = content.split("\n")
    current_section = None

    for i, line in enumerate(lines):
        if line.startswith("# "):
            week_label = line[2:].strip()
        elif line.startswith("## "):
            name = line[3:].strip()
            current_section = name if name in SECTIONS else None
        elif current_section is not None:
            if line.startswith("  ") and sections[current_section]:
                # Indented note line for the last task in this section
                note_text = line[2:].strip()
                if note_text:
                    last = sections[current_section][-1]
                    existing = last.get("note") or ""
                    last["note"] = (existing + "\n" + note_text).strip() if existing else note_text
            else:
                task = _parse_task_line(line, i)
                if task:
                    task["section_idx"] = len(sections[current_section])
                    sections[current_section].append(task)

    return {"week_label": week_label, "sections": sections}


def _build_file(monday: date, sunday: date, sections: dict) -> str:
    """Build markdown content for a week file."""
    week_num = monday.isocalendar()[1]
    header = (
        f"# Week {week_num} — "
        f"{MONTH_ABBR[monday.month-1]} {monday.day}–"
        f"{MONTH_ABBR[sunday.month-1]} {sunday.day}"
    )

    lines = [header, ""]

    for section in SECTIONS:
        lines.append(f"## {section}")
        for task in sections.get(section, []):
            state = "x" if task.get("checked") else " "
            raw = task["text"]
            if task.get("recur"):
                raw += f" [recur:{task['recur']}]"
            if task.get("attachment"):
                raw += f" [attach:{task['attachment']}]"
            lines.append(f"- [{state}] {raw}")
            if task.get("note"):
                lines.append(f"  {task['note']}")
        lines.append("")

    return "\n".join(lines)


def _save_sections(date_str: str, sections: dict) -> dict:
    """Rebuild and save the week file with updated sections."""
    monday, sunday = get_week_bounds(date_str)
    path = get_week_path(date_str)
    content = _build_file(monday, sunday, sections)
    path.write_text(content, encoding="utf-8")
    return {"status": "saved"}


def load_recurring() -> list:
    """Load global recurring task names from WeeklyRecurring.md."""
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


def get_or_create_week(date_str: str) -> dict:
    """Return full week data for the API. Creates file if needed."""
    monday, sunday = get_week_bounds(date_str)
    path = get_week_path(date_str)

    if not path.exists():
        sections = {s: [] for s in SECTIONS}

        # Carry forward from previous week: recurring tasks + uncompleted Someday
        prev_monday = monday - timedelta(days=1)
        prev_path = get_week_path(prev_monday.isoformat())
        if prev_path.exists():
            prev = _parse_file(prev_path)
            for section in SECTIONS:
                for t in prev["sections"].get(section, []):
                    if not t["checked"] and (t.get("recur") or section == "Someday"):
                        sections[section].append({
                            "text": t["text"],
                            "checked": False,
                            "recur": t.get("recur"),
                            "attachment": t.get("attachment"),
                        })

        path.write_text(_build_file(monday, sunday, sections), encoding="utf-8")

    parsed = _parse_file(path)
    recurring = load_recurring()

    today = date.today()
    week_num = monday.isocalendar()[1]

    days = []
    for i, name in enumerate(DAY_NAMES):
        d = monday + timedelta(days=i)
        days.append({
            "name": name,
            "short": name[:3].upper(),
            "date_num": d.day,
            "date_str": d.isoformat(),
            "is_today": d == today,
            "tasks": parsed["sections"].get(name, []),
        })

    return {
        "week_label": parsed["week_label"],
        "week_num": week_num,
        "monday": monday.isoformat(),
        "sunday": sunday.isoformat(),
        "header": (
            f"{MONTH_ABBR[monday.month-1]} {monday.day}–"
            f"{MONTH_ABBR[sunday.month-1]} {sunday.day}"
        ),
        "days": days,
        "someday": parsed["sections"].get("Someday", []),
        "recurring": recurring,
    }


def add_task(date_str: str, section: str, text: str, recur: str = None, note: str = None) -> dict:
    """Append a new task to a section."""
    if section not in SECTIONS:
        return {"error": f"Invalid section: {section}"}
    text = text.strip()
    if not text:
        return {"error": "Task text required"}

    path = get_week_path(date_str)
    if not path.exists():
        get_or_create_week(date_str)

    parsed = _parse_file(path)
    parsed["sections"][section].append({
        "text": text,
        "checked": False,
        "recur": recur or None,
        "attachment": None,
        "note": note.strip() if note else None,
    })
    return _save_sections(date_str, parsed["sections"])


def toggle_task(date_str: str, section: str, section_idx: int, checked: bool) -> dict:
    """Toggle checkbox at a specific section position."""
    if section not in SECTIONS:
        return {"error": "Invalid section"}

    path = get_week_path(date_str)
    if not path.exists():
        return {"error": "Week file not found"}

    parsed = _parse_file(path)
    tasks = parsed["sections"].get(section, [])
    if section_idx < 0 or section_idx >= len(tasks):
        return {"error": "Task not found"}

    tasks[section_idx]["checked"] = checked
    parsed["sections"][section] = tasks
    return _save_sections(date_str, parsed["sections"])


def delete_task(date_str: str, section: str, section_idx: int) -> dict:
    """Delete a task from a section by section position."""
    if section not in SECTIONS:
        return {"error": "Invalid section"}

    path = get_week_path(date_str)
    if not path.exists():
        return {"error": "Week file not found"}

    parsed = _parse_file(path)
    tasks = parsed["sections"].get(section, [])
    if section_idx < 0 or section_idx >= len(tasks):
        return {"error": "Task not found"}

    del tasks[section_idx]
    parsed["sections"][section] = tasks
    return _save_sections(date_str, parsed["sections"])


def set_task_recur(date_str: str, section: str, section_idx: int, recur: str) -> dict:
    """Set or clear task recurrence metadata."""
    if section not in SECTIONS:
        return {"error": "Invalid section"}

    path = get_week_path(date_str)
    if not path.exists():
        return {"error": "Week file not found"}

    parsed = _parse_file(path)
    tasks = parsed["sections"].get(section, [])
    if section_idx < 0 or section_idx >= len(tasks):
        return {"error": "Task not found"}

    tasks[section_idx]["recur"] = recur.strip() or None
    parsed["sections"][section] = tasks
    return _save_sections(date_str, parsed["sections"])


def defer_task(date_str: str, section: str, section_idx: int, defer_to: str) -> dict:
    """Move a task to a different section or week."""
    if section not in SECTIONS:
        return {"error": "Invalid section"}

    path = get_week_path(date_str)
    if not path.exists():
        return {"error": "Week file not found"}

    parsed = _parse_file(path)
    tasks = parsed["sections"].get(section, [])
    if section_idx < 0 or section_idx >= len(tasks):
        return {"error": "Task not found"}
    task = tasks[section_idx]

    # Determine target date_str and section
    if defer_to == "someday":
        if section == "Someday":
            return {"error": "Already in Someday"}
        target_date_str = date_str
        target_section = "Someday"

    elif defer_to == "tomorrow":
        if section == "Someday":
            return {"error": "Cannot defer Someday task to tomorrow"}
        tomorrow = date.fromisoformat(date_str) + timedelta(days=1)
        target_date_str = tomorrow.isoformat()
        target_section = tomorrow.strftime("%A")

    elif defer_to == "next_week":
        if section == "Someday":
            monday, _ = get_week_bounds(date_str)
            next_monday = monday + timedelta(days=7)
            target_date_str = next_monday.isoformat()
            target_section = "Someday"
        else:
            next_week_day = date.fromisoformat(date_str) + timedelta(days=7)
            target_date_str = next_week_day.isoformat()
            target_section = next_week_day.strftime("%A")

    else:
        return {"error": f"Unknown defer_to: {defer_to}"}

    # Check if source and target are in the same week file
    source_monday, _ = get_week_bounds(date_str)
    target_monday, _ = get_week_bounds(target_date_str)

    # Remove from current section
    parsed["sections"][section] = [t for t in tasks if t is not task]

    if source_monday == target_monday:
        # Same file — add to target section
        parsed["sections"][target_section].append({
            "text": task["text"],
            "checked": False,
            "recur": task.get("recur"),
            "attachment": task.get("attachment"),
            "note": task.get("note"),
        })
        return _save_sections(date_str, parsed["sections"])
    else:
        # Different file — save removal, then add to target week
        _save_sections(date_str, parsed["sections"])
        return add_task(target_date_str, target_section, task["text"], task.get("recur"), task.get("note"))


def duplicate_task(date_str: str, section: str, section_idx: int) -> dict:
    """Insert a copy of a task right after its original position."""
    if section not in SECTIONS:
        return {"error": "Invalid section"}

    path = get_week_path(date_str)
    if not path.exists():
        return {"error": "Week file not found"}

    parsed = _parse_file(path)
    tasks = parsed["sections"].get(section, [])
    if section_idx < 0 or section_idx >= len(tasks):
        return {"error": "Task not found"}
    task = tasks[section_idx]
    idx = section_idx
    new_task = {
        "text": task["text"],
        "checked": False,
        "recur": task.get("recur"),
        "attachment": task.get("attachment"),
        "note": task.get("note"),
    }
    tasks.insert(idx + 1, new_task)
    parsed["sections"][section] = tasks
    return _save_sections(date_str, parsed["sections"])


def attach_file(date_str: str, section: str, section_idx: int, filename: str) -> dict:
    """Set or clear file attachment on a task."""
    if section not in SECTIONS:
        return {"error": "Invalid section"}

    path = get_week_path(date_str)
    if not path.exists():
        return {"error": "Week file not found"}

    parsed = _parse_file(path)
    tasks = parsed["sections"].get(section, [])
    if section_idx < 0 or section_idx >= len(tasks):
        return {"error": "Task not found"}

    tasks[section_idx]["attachment"] = filename.strip() or None
    parsed["sections"][section] = tasks
    return _save_sections(date_str, parsed["sections"])


def set_task_note(date_str: str, section: str, section_idx: int, note: str) -> dict:
    """Set or clear the note on a task."""
    if section not in SECTIONS:
        return {"error": "Invalid section"}

    path = get_week_path(date_str)
    if not path.exists():
        return {"error": "Week file not found"}

    parsed = _parse_file(path)
    tasks = parsed["sections"].get(section, [])
    if section_idx < 0 or section_idx >= len(tasks):
        return {"error": "Task not found"}

    tasks[section_idx]["note"] = note.strip() or None
    parsed["sections"][section] = tasks
    return _save_sections(date_str, parsed["sections"])


def rename_task(date_str: str, section: str, section_idx: int, new_text: str) -> dict:
    """Rename a task's display text."""
    if section not in SECTIONS:
        return {"error": "Invalid section"}
    new_text = new_text.strip()
    if not new_text:
        return {"error": "Task text required"}

    path = get_week_path(date_str)
    if not path.exists():
        return {"error": "Week file not found"}

    parsed = _parse_file(path)
    tasks = parsed["sections"].get(section, [])
    if section_idx < 0 or section_idx >= len(tasks):
        return {"error": "Task not found"}

    tasks[section_idx]["text"] = new_text
    parsed["sections"][section] = tasks
    return _save_sections(date_str, parsed["sections"])


def reorder_section(date_str: str, section: str, ordered_texts: list) -> dict:
    """Reorder tasks in a section by text list."""
    if section not in SECTIONS:
        return {"error": "Invalid section"}

    path = get_week_path(date_str)
    if not path.exists():
        return {"error": "Week file not found"}

    parsed = _parse_file(path)
    tasks = parsed["sections"].get(section, [])

    task_map = {t["text"]: t for t in tasks}
    new_order = [task_map[text] for text in ordered_texts if text in task_map]

    # Append any tasks not in the ordered list (safety)
    seen = set(ordered_texts)
    for t in tasks:
        if t["text"] not in seen:
            new_order.append(t)

    parsed["sections"][section] = new_order
    return _save_sections(date_str, parsed["sections"])
