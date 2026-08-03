"""Tests for daemons/weekplan.py — uses a temp DB, patches config paths."""

import sys
import types
import tempfile
import unittest
from pathlib import Path
from datetime import date, timedelta
from unittest.mock import patch


# ── Stub out config before importing weekplan ─────────────────────────────────

_tmp_dir = tempfile.mkdtemp()
_fake_config = types.SimpleNamespace(
    DATA_DIR=Path(_tmp_dir),
    TEMPLATES_DIR=Path(_tmp_dir),
)
sys.modules["config"] = _fake_config  # type: ignore

from daemons import weekplan  # noqa: E402  (must come after stub)


def _reset_db():
    """Drop and recreate the test database."""
    db = Path(_tmp_dir) / "athena.db"
    if db.exists():
        db.unlink()
    weekplan.DB_PATH = db
    weekplan.init_db()


MONDAY = "2025-06-02"   # a known Monday
TUESDAY = "2025-06-03"
WEDNESDAY = "2025-06-04"
THURSDAY = "2025-06-05"
FRIDAY = "2025-06-06"
SATURDAY = "2025-06-07"
SUNDAY = "2025-06-08"

NEXT_MON = "2025-06-09"
WEEK3 = "2025-06-16"
WEEK4 = "2025-06-23"


def _mon(weeks_ahead: int) -> str:
    return (date.fromisoformat(MONDAY) + timedelta(days=7 * weeks_ahead)).isoformat()


# ── Add / Get ─────────────────────────────────────────────────────────────────

class TestAddAndGet(unittest.TestCase):
    def setUp(self):
        _reset_db()

    def test_add_task_appears_in_week(self):
        weekplan.add_task(MONDAY, "Monday", "Buy milk")
        data = weekplan.get_or_create_week(MONDAY)
        mon = next(d for d in data["days"] if d["name"] == "Monday")
        self.assertEqual(len(mon["items"]), 1)
        self.assertEqual(mon["items"][0]["text"], "Buy milk")

    def test_add_task_invalid_section(self):
        result = weekplan.add_task(MONDAY, "Funday", "Bad task")
        self.assertIn("error", result)

    def test_add_task_empty_text(self):
        result = weekplan.add_task(MONDAY, "Monday", "   ")
        self.assertIn("error", result)

    def test_add_multiple_tasks_ordered(self):
        weekplan.add_task(MONDAY, "Monday", "First")
        weekplan.add_task(MONDAY, "Monday", "Second")
        weekplan.add_task(MONDAY, "Monday", "Third")
        data = weekplan.get_or_create_week(MONDAY)
        mon = next(d for d in data["days"] if d["name"] == "Monday")
        self.assertEqual([t["text"] for t in mon["items"]], ["First", "Second", "Third"])

    def test_someday_task(self):
        weekplan.add_task(MONDAY, "Someday", "Rainy day idea")
        data = weekplan.get_or_create_week(MONDAY)
        self.assertEqual(len(data["someday"]), 1)
        self.assertEqual(data["someday"][0]["text"], "Rainy day idea")

    def test_scratchpad_task(self):
        weekplan.add_task(MONDAY, "Scratchpad", "Scratch note")
        data = weekplan.get_or_create_week(MONDAY)
        self.assertEqual(len(data["scratchpad"]), 1)
        self.assertEqual(data["scratchpad"][0]["text"], "Scratch note")

    def test_add_tasks_to_all_day_sections(self):
        for day in weekplan.DAY_NAMES:
            weekplan.add_task(MONDAY, day, f"Task for {day}")
        data = weekplan.get_or_create_week(MONDAY)
        for col in data["days"]:
            self.assertEqual(col["items"][0]["text"], f"Task for {col['name']}")

    def test_week_header_present(self):
        data = weekplan.get_or_create_week(MONDAY)
        self.assertIn("header", data)
        self.assertTrue(len(data["header"]) > 0)

    def test_week_data_has_seven_days(self):
        data = weekplan.get_or_create_week(MONDAY)
        self.assertEqual(len(data["days"]), 7)

    def test_task_fields_present(self):
        weekplan.add_task(MONDAY, "Monday", "Check fields")
        data = weekplan.get_or_create_week(MONDAY)
        item = data["days"][0]["items"][0]
        for field in ("text", "checked", "recur", "note", "section_idx", "color"):
            self.assertIn(field, item)

    def test_non_monday_date_resolves_to_week(self):
        weekplan.add_task(WEDNESDAY, "Wednesday", "Mid-week task")
        data = weekplan.get_or_create_week(TUESDAY)
        wed = next(d for d in data["days"] if d["name"] == "Wednesday")
        self.assertEqual(wed["items"][0]["text"], "Mid-week task")


# ── Toggle ────────────────────────────────────────────────────────────────────

class TestToggle(unittest.TestCase):
    def setUp(self):
        _reset_db()
        weekplan.add_task(MONDAY, "Monday", "Task A")

    def test_toggle_check(self):
        result = weekplan.toggle_task(MONDAY, "Monday", 0, True)
        self.assertEqual(result["status"], "saved")
        data = weekplan.get_or_create_week(MONDAY)
        mon = next(d for d in data["days"] if d["name"] == "Monday")
        self.assertTrue(mon["items"][0]["checked"])

    def test_toggle_uncheck(self):
        weekplan.toggle_task(MONDAY, "Monday", 0, True)
        weekplan.toggle_task(MONDAY, "Monday", 0, False)
        data = weekplan.get_or_create_week(MONDAY)
        mon = next(d for d in data["days"] if d["name"] == "Monday")
        self.assertFalse(mon["items"][0]["checked"])

    def test_toggle_invalid_idx(self):
        result = weekplan.toggle_task(MONDAY, "Monday", 99, True)
        self.assertIn("error", result)

    def test_toggle_sets_completed_at(self):
        weekplan.toggle_task(MONDAY, "Monday", 0, True)
        with weekplan.get_db() as conn:
            row = conn.execute("SELECT completed_at FROM tasks WHERE section='Monday'").fetchone()
        self.assertIsNotNone(row["completed_at"])

    def test_uncheck_clears_completed_at(self):
        weekplan.toggle_task(MONDAY, "Monday", 0, True)
        weekplan.toggle_task(MONDAY, "Monday", 0, False)
        with weekplan.get_db() as conn:
            row = conn.execute("SELECT completed_at FROM tasks WHERE section='Monday'").fetchone()
        self.assertIsNone(row["completed_at"])

    def test_toggle_invalid_section(self):
        result = weekplan.toggle_task(MONDAY, "Funday", 0, True)
        self.assertIn("error", result)


# ── Delete ────────────────────────────────────────────────────────────────────

class TestDelete(unittest.TestCase):
    def setUp(self):
        _reset_db()
        weekplan.add_task(MONDAY, "Monday", "Alpha")
        weekplan.add_task(MONDAY, "Monday", "Beta")
        weekplan.add_task(MONDAY, "Monday", "Gamma")

    def test_delete_middle(self):
        weekplan.delete_task(MONDAY, "Monday", 1)
        data = weekplan.get_or_create_week(MONDAY)
        mon = next(d for d in data["days"] if d["name"] == "Monday")
        self.assertEqual([t["text"] for t in mon["items"]], ["Alpha", "Gamma"])

    def test_delete_renormalises_indices(self):
        weekplan.delete_task(MONDAY, "Monday", 0)
        data = weekplan.get_or_create_week(MONDAY)
        mon = next(d for d in data["days"] if d["name"] == "Monday")
        self.assertEqual(mon["items"][0]["section_idx"], 0)
        self.assertEqual(mon["items"][1]["section_idx"], 1)

    def test_delete_invalid_idx(self):
        result = weekplan.delete_task(MONDAY, "Monday", 99)
        self.assertIn("error", result)

    def test_delete_first(self):
        weekplan.delete_task(MONDAY, "Monday", 0)
        data = weekplan.get_or_create_week(MONDAY)
        mon = next(d for d in data["days"] if d["name"] == "Monday")
        self.assertEqual(mon["items"][0]["text"], "Beta")

    def test_delete_last(self):
        weekplan.delete_task(MONDAY, "Monday", 2)
        data = weekplan.get_or_create_week(MONDAY)
        mon = next(d for d in data["days"] if d["name"] == "Monday")
        self.assertEqual(len(mon["items"]), 2)
        self.assertEqual(mon["items"][-1]["text"], "Beta")


# ── Rename ────────────────────────────────────────────────────────────────────

class TestRename(unittest.TestCase):
    def setUp(self):
        _reset_db()
        weekplan.add_task(MONDAY, "Tuesday", "Old name")

    def test_rename(self):
        weekplan.rename_task(TUESDAY, "Tuesday", 0, "New name")
        data = weekplan.get_or_create_week(MONDAY)
        tue = next(d for d in data["days"] if d["name"] == "Tuesday")
        self.assertEqual(tue["items"][0]["text"], "New name")

    def test_rename_empty_fails(self):
        result = weekplan.rename_task(TUESDAY, "Tuesday", 0, "  ")
        self.assertIn("error", result)

    def test_rename_whitespace_stripped(self):
        weekplan.rename_task(TUESDAY, "Tuesday", 0, "  Trimmed  ")
        data = weekplan.get_or_create_week(MONDAY)
        tue = next(d for d in data["days"] if d["name"] == "Tuesday")
        self.assertEqual(tue["items"][0]["text"], "Trimmed")

    def test_rename_invalid_idx(self):
        result = weekplan.rename_task(TUESDAY, "Tuesday", 99, "Nope")
        self.assertIn("error", result)


# ── Note ──────────────────────────────────────────────────────────────────────

class TestNote(unittest.TestCase):
    def setUp(self):
        _reset_db()
        weekplan.add_task(MONDAY, "Wednesday", "Noted task")

    def test_set_note(self):
        weekplan.set_task_note(WEDNESDAY, "Wednesday", 0, "Here's a note")
        data = weekplan.get_or_create_week(MONDAY)
        wed = next(d for d in data["days"] if d["name"] == "Wednesday")
        self.assertEqual(wed["items"][0]["note"], "Here's a note")

    def test_clear_note(self):
        weekplan.set_task_note(WEDNESDAY, "Wednesday", 0, "Note")
        weekplan.set_task_note(WEDNESDAY, "Wednesday", 0, "")
        data = weekplan.get_or_create_week(MONDAY)
        wed = next(d for d in data["days"] if d["name"] == "Wednesday")
        self.assertIsNone(wed["items"][0]["note"])

    def test_note_multiline(self):
        weekplan.set_task_note(WEDNESDAY, "Wednesday", 0, "Line 1\nLine 2\nLine 3")
        data = weekplan.get_or_create_week(MONDAY)
        wed = next(d for d in data["days"] if d["name"] == "Wednesday")
        self.assertIn("\n", wed["items"][0]["note"])


# ── Reorder ───────────────────────────────────────────────────────────────────

class TestReorder(unittest.TestCase):
    def setUp(self):
        _reset_db()
        weekplan.add_task(MONDAY, "Monday", "Alpha")
        weekplan.add_task(MONDAY, "Monday", "Beta")
        weekplan.add_task(MONDAY, "Monday", "Gamma")

    def test_reorder_within_section(self):
        weekplan.reorder_section(MONDAY, "Monday", ["Gamma", "Alpha", "Beta"])
        data = weekplan.get_or_create_week(MONDAY)
        mon = next(d for d in data["days"] if d["name"] == "Monday")
        self.assertEqual([t["text"] for t in mon["items"]], ["Gamma", "Alpha", "Beta"])

    def test_reorder_partial_list(self):
        weekplan.reorder_section(MONDAY, "Monday", ["Beta"])
        data = weekplan.get_or_create_week(MONDAY)
        mon = next(d for d in data["days"] if d["name"] == "Monday")
        self.assertIn("Beta", [t["text"] for t in mon["items"]])

    def test_reorder_invalid_section(self):
        result = weekplan.reorder_section(MONDAY, "Funday", ["Alpha"])
        self.assertIn("error", result)

    def test_reorder_someday(self):
        weekplan.add_task(MONDAY, "Someday", "S1")
        weekplan.add_task(MONDAY, "Someday", "S2")
        weekplan.add_task(MONDAY, "Someday", "S3")
        weekplan.reorder_section(MONDAY, "Someday", ["S3", "S1", "S2"])
        data = weekplan.get_or_create_week(MONDAY)
        self.assertEqual([t["text"] for t in data["someday"]], ["S3", "S1", "S2"])

    def test_reorder_idempotent(self):
        weekplan.reorder_section(MONDAY, "Monday", ["Alpha", "Beta", "Gamma"])
        data = weekplan.get_or_create_week(MONDAY)
        mon = next(d for d in data["days"] if d["name"] == "Monday")
        self.assertEqual([t["text"] for t in mon["items"]], ["Alpha", "Beta", "Gamma"])


# ── Defer ─────────────────────────────────────────────────────────────────────

class TestDefer(unittest.TestCase):
    def setUp(self):
        _reset_db()
        weekplan.add_task(MONDAY, "Monday", "Defer me")

    def test_defer_to_tomorrow(self):
        weekplan.defer_task(MONDAY, "Monday", 0, "tomorrow")
        data = weekplan.get_or_create_week(MONDAY)
        mon = next(d for d in data["days"] if d["name"] == "Monday")
        tue = next(d for d in data["days"] if d["name"] == "Tuesday")
        self.assertEqual(len(mon["items"]), 0)
        self.assertEqual(tue["items"][0]["text"], "Defer me")

    def test_defer_to_someday(self):
        weekplan.defer_task(MONDAY, "Monday", 0, "someday")
        data = weekplan.get_or_create_week(MONDAY)
        self.assertEqual(len(data["someday"]), 1)
        self.assertEqual(len(next(d for d in data["days"] if d["name"] == "Monday")["items"]), 0)

    def test_defer_to_next_week(self):
        weekplan.defer_task(MONDAY, "Monday", 0, "next_week")
        data = weekplan.get_or_create_week(NEXT_MON)
        mon = next(d for d in data["days"] if d["name"] == "Monday")
        self.assertEqual(len(mon["items"]), 1)

    def test_defer_to_weekend(self):
        weekplan.defer_task(MONDAY, "Monday", 0, "weekend")
        data = weekplan.get_or_create_week(MONDAY)
        sat = next(d for d in data["days"] if d["name"] == "Saturday")
        self.assertEqual(sat["items"][0]["text"], "Defer me")

    def test_defer_unknown_target(self):
        result = weekplan.defer_task(MONDAY, "Monday", 0, "never")
        self.assertIn("error", result)

    def test_defer_from_end_of_week_tomorrow(self):
        weekplan.add_task(MONDAY, "Sunday", "Last day task")
        weekplan.defer_task(MONDAY, "Sunday", 0, "tomorrow")
        next_data = weekplan.get_or_create_week(NEXT_MON)
        mon = next(d for d in next_data["days"] if d["name"] == "Monday")
        self.assertEqual(mon["items"][0]["text"], "Last day task")

    def test_defer_preserves_note(self):
        weekplan.set_task_note(MONDAY, "Monday", 0, "important context")
        weekplan.defer_task(MONDAY, "Monday", 0, "tomorrow")
        data = weekplan.get_or_create_week(MONDAY)
        tue = next(d for d in data["days"] if d["name"] == "Tuesday")
        self.assertEqual(tue["items"][0]["note"], "important context")


# ── Duplicate ─────────────────────────────────────────────────────────────────

class TestDuplicate(unittest.TestCase):
    def setUp(self):
        _reset_db()
        weekplan.add_task(MONDAY, "Monday", "Original")

    def test_duplicate_creates_copy(self):
        weekplan.duplicate_task(MONDAY, "Monday", 0)
        data = weekplan.get_or_create_week(MONDAY)
        mon = next(d for d in data["days"] if d["name"] == "Monday")
        self.assertEqual([t["text"] for t in mon["items"]], ["Original", "Original"])

    def test_duplicate_preserves_note(self):
        weekplan.set_task_note(MONDAY, "Monday", 0, "keep this")
        weekplan.duplicate_task(MONDAY, "Monday", 0)
        data = weekplan.get_or_create_week(MONDAY)
        mon = next(d for d in data["days"] if d["name"] == "Monday")
        self.assertEqual(mon["items"][1]["note"], "keep this")

    def test_duplicate_invalid_idx(self):
        result = weekplan.duplicate_task(MONDAY, "Monday", 99)
        self.assertIn("error", result)

    def test_duplicate_is_unchecked(self):
        weekplan.toggle_task(MONDAY, "Monday", 0, True)
        weekplan.duplicate_task(MONDAY, "Monday", 0)
        data = weekplan.get_or_create_week(MONDAY)
        mon = next(d for d in data["days"] if d["name"] == "Monday")
        self.assertFalse(mon["items"][1]["checked"])


# ── Recurrence (set on task) ──────────────────────────────────────────────────

class TestRecur(unittest.TestCase):
    def setUp(self):
        _reset_db()
        weekplan.add_task(MONDAY, "Monday", "Daily task")

    def test_set_recur(self):
        weekplan.set_task_recur(MONDAY, "Monday", 0, "daily")
        data = weekplan.get_or_create_week(MONDAY)
        mon = next(d for d in data["days"] if d["name"] == "Monday")
        self.assertEqual(mon["items"][0]["recur"], "daily")

    def test_daily_recur_expands_to_all_days(self):
        weekplan.set_task_recur(MONDAY, "Monday", 0, "daily")
        data = weekplan.get_or_create_week(MONDAY)
        for day in weekplan.DAY_NAMES:
            col = next(d for d in data["days"] if d["name"] == day)
            self.assertIn("Daily task", [t["text"] for t in col["items"]], f"Missing from {day}")

    def test_weekdays_recur_skips_weekend(self):
        weekplan.set_task_recur(MONDAY, "Monday", 0, "weekdays")
        data = weekplan.get_or_create_week(MONDAY)
        for day in ["Saturday", "Sunday"]:
            col = next(d for d in data["days"] if d["name"] == day)
            self.assertNotIn("Daily task", [t["text"] for t in col["items"]])
        for day in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]:
            col = next(d for d in data["days"] if d["name"] == day)
            self.assertIn("Daily task", [t["text"] for t in col["items"]])

    def test_clear_recur(self):
        weekplan.set_task_recur(MONDAY, "Monday", 0, "daily")
        weekplan.set_task_recur(MONDAY, "Monday", 0, "")
        data = weekplan.get_or_create_week(MONDAY)
        mon = next(d for d in data["days"] if d["name"] == "Monday")
        self.assertIsNone(mon["items"][0]["recur"])

    def test_set_recur_invalid_section(self):
        result = weekplan.set_task_recur(MONDAY, "Funday", 0, "weekly")
        self.assertIn("error", result)

    def test_set_recur_invalid_idx(self):
        result = weekplan.set_task_recur(MONDAY, "Monday", 99, "weekly")
        self.assertIn("error", result)


# ── Carry-forward ─────────────────────────────────────────────────────────────

class TestCarryForward(unittest.TestCase):
    def setUp(self):
        _reset_db()

    def test_unchecked_recurring_carried(self):
        weekplan.add_task(MONDAY, "Monday", "Weekly review")
        weekplan.set_task_recur(MONDAY, "Monday", 0, "weekly")
        data = weekplan.get_or_create_week(NEXT_MON)
        mon = next(d for d in data["days"] if d["name"] == "Monday")
        self.assertIn("Weekly review", [t["text"] for t in mon["items"]])

    def test_checked_recurring_still_carried_unchecked(self):
        # Recurring tasks always carry — checked state resets to 0 in the new week
        weekplan.add_task(MONDAY, "Monday", "Done task")
        weekplan.set_task_recur(MONDAY, "Monday", 0, "weekly")
        weekplan.toggle_task(MONDAY, "Monday", 0, True)
        data = weekplan.get_or_create_week(NEXT_MON)
        mon = next(d for d in data["days"] if d["name"] == "Monday")
        self.assertIn("Done task", [t["text"] for t in mon["items"]])
        self.assertFalse(mon["items"][0]["checked"])

    def test_someday_tasks_carried_to_new_week(self):
        weekplan.add_task(MONDAY, "Someday", "Persistent idea")
        data = weekplan.get_or_create_week(NEXT_MON)
        self.assertIn("Persistent idea", [t["text"] for t in data["someday"]])

    def test_someday_tasks_visible_across_weeks(self):
        # Someday is a global backlog (stored with SOMEDAY_WEEK sentinel),
        # so tasks always appear regardless of which week is loaded.
        weekplan.add_task(MONDAY, "Someday", "Global idea")
        data = weekplan.get_or_create_week(WEEK4)
        self.assertIn("Global idea", [t["text"] for t in data["someday"]])

    def test_scratchpad_never_carried(self):
        weekplan.add_task(MONDAY, "Scratchpad", "Ephemeral note")
        data = weekplan.get_or_create_week(NEXT_MON)
        self.assertEqual(len(data["scratchpad"]), 0)

    def test_non_recurring_day_task_not_carried(self):
        weekplan.add_task(MONDAY, "Monday", "One-off task")
        data = weekplan.get_or_create_week(NEXT_MON)
        mon = next(d for d in data["days"] if d["name"] == "Monday")
        self.assertNotIn("One-off task", [t["text"] for t in mon["items"]])

    def test_biweekly_skips_alternate_weeks(self):
        weekplan.add_task(MONDAY, "Monday", "Bi-weekly")
        weekplan.set_task_recur(MONDAY, "Monday", 0, "biweekly")

        data2 = weekplan.get_or_create_week(NEXT_MON)
        mon2 = next(d for d in data2["days"] if d["name"] == "Monday")
        self.assertNotIn("Bi-weekly", [t["text"] for t in mon2["items"]], "Should skip week 2")

        data3 = weekplan.get_or_create_week(WEEK3)
        mon3 = next(d for d in data3["days"] if d["name"] == "Monday")
        self.assertIn("Bi-weekly", [t["text"] for t in mon3["items"]], "Should appear week 3")

    def test_biweekly_week4_appears(self):
        weekplan.add_task(MONDAY, "Monday", "Bi-weekly")
        weekplan.set_task_recur(MONDAY, "Monday", 0, "biweekly")
        weekplan.get_or_create_week(NEXT_MON)   # skip
        weekplan.get_or_create_week(WEEK3)       # appear
        data4 = weekplan.get_or_create_week(WEEK4)
        mon4 = next(d for d in data4["days"] if d["name"] == "Monday")
        self.assertNotIn("Bi-weekly", [t["text"] for t in mon4["items"]], "Should skip week 4")

    def test_carry_forward_not_duplicated_on_second_load(self):
        weekplan.add_task(MONDAY, "Monday", "Weekly task")
        weekplan.set_task_recur(MONDAY, "Monday", 0, "weekly")
        weekplan.get_or_create_week(NEXT_MON)
        data = weekplan.get_or_create_week(NEXT_MON)  # second load
        mon = next(d for d in data["days"] if d["name"] == "Monday")
        count = sum(1 for t in mon["items"] if t["text"] == "Weekly task")
        self.assertEqual(count, 1)

    def test_monthly_appears_in_correct_week(self):
        # Origin 2025-06-15 (Sunday). July 15 is a Tuesday → task appears in Tuesday column
        weekplan.add_task(MONDAY, "Sunday", "Monthly report")
        with weekplan.get_db() as conn:
            conn.execute(
                "UPDATE tasks SET recur='monthly', created_at=? WHERE text='Monthly report'",
                ("2025-06-15T00:00:00",),
            )
        # July 15 falls in the week of 2025-07-14, in the Tuesday column
        data = weekplan.get_or_create_week("2025-07-14")
        tue = next(d for d in data["days"] if d["name"] == "Tuesday")
        self.assertIn("Monthly report", [t["text"] for t in tue["items"]])

    def test_monthly_skips_wrong_week(self):
        weekplan.add_task(MONDAY, "Sunday", "Monthly skip test")
        with weekplan.get_db() as conn:
            conn.execute(
                "UPDATE tasks SET recur='monthly', created_at=? WHERE text='Monthly skip test'",
                ("2025-06-15T00:00:00",),
            )
        # July 7 week does NOT contain July 15 — task should not appear anywhere
        data = weekplan.get_or_create_week("2025-07-07")
        all_items = [t["text"] for d in data["days"] for t in d["items"]]
        self.assertNotIn("Monthly skip test", all_items)

    def test_every_3_weeks_pattern(self):
        weekplan.add_task(MONDAY, "Monday", "Every 3 weeks")
        weekplan.set_task_recur(MONDAY, "Monday", 0, "every 3 weeks")
        w2 = weekplan.get_or_create_week(_mon(1))
        w3 = weekplan.get_or_create_week(_mon(2))
        w4 = weekplan.get_or_create_week(_mon(3))
        def _has(data): return "Every 3 weeks" in [t["text"] for t in next(d for d in data["days"] if d["name"] == "Monday")["items"]]
        self.assertFalse(_has(w2))
        self.assertFalse(_has(w3))
        self.assertTrue(_has(w4))


# ── Recurring Manager ─────────────────────────────────────────────────────────

class TestRecurringManager(unittest.TestCase):
    def setUp(self):
        _reset_db()
        weekplan.get_or_create_week(MONDAY)  # initialize week

    def test_add_recurring_appears_this_week(self):
        weekplan.add_recurring_task("Stand-up", "Monday", "daily", MONDAY)
        data = weekplan.get_or_create_week(MONDAY)
        mon = next(d for d in data["days"] if d["name"] == "Monday")
        self.assertIn("Stand-up", [t["text"] for t in mon["items"]])

    def test_add_recurring_listed(self):
        weekplan.add_recurring_task("Inbox zero", "Monday", "weekly", MONDAY)
        items = weekplan.list_recurring_tasks()
        texts = [i["text"] for i in items]
        self.assertIn("Inbox zero", texts)

    def test_add_recurring_duplicate_same_week_rejected(self):
        weekplan.add_recurring_task("Stand-up", "Monday", "daily", MONDAY)
        result = weekplan.add_recurring_task("Stand-up", "Monday", "daily", MONDAY)
        self.assertIn("error", result)

    def test_add_recurring_empty_text_rejected(self):
        result = weekplan.add_recurring_task("  ", "Monday", "daily", MONDAY)
        self.assertIn("error", result)

    def test_add_recurring_invalid_section_rejected(self):
        result = weekplan.add_recurring_task("Task", "Funday", "daily", MONDAY)
        self.assertIn("error", result)

    def test_add_recurring_missing_recur_rejected(self):
        result = weekplan.add_recurring_task("Task", "Monday", "", MONDAY)
        self.assertIn("error", result)

    def test_delete_recurring_removes_all_instances(self):
        weekplan.add_recurring_task("Clean desk", "Monday", "weekly", MONDAY)
        weekplan.get_or_create_week(NEXT_MON)  # creates instance in week 2
        weekplan.delete_all_recurring("Clean desk", "weekly")
        items = weekplan.list_recurring_tasks()
        self.assertNotIn("Clean desk", [i["text"] for i in items])

    def test_forward_fill_already_initialized_weeks(self):
        # Initialize next week BEFORE adding the recurring task
        weekplan.get_or_create_week(NEXT_MON)
        weekplan.add_recurring_task("Late addition", "Monday", "weekly", MONDAY)
        # Next week is already in weeks_initialized — forward-fill must inject the task
        data = weekplan.get_or_create_week(NEXT_MON)
        mon = next(d for d in data["days"] if d["name"] == "Monday")
        self.assertIn("Late addition", [t["text"] for t in mon["items"]])

    def test_forward_fill_two_initialized_weeks(self):
        weekplan.get_or_create_week(NEXT_MON)
        weekplan.get_or_create_week(WEEK3)
        weekplan.add_recurring_task("Late weekly", "Monday", "weekly", MONDAY)
        for wk in (NEXT_MON, WEEK3):
            data = weekplan.get_or_create_week(wk)
            mon = next(d for d in data["days"] if d["name"] == "Monday")
            self.assertIn("Late weekly", [t["text"] for t in mon["items"]], f"Missing from {wk}")

    def test_forward_fill_respects_should_carry(self):
        # Biweekly: initialized week 2 should get it, initialized week 3 should not
        weekplan.get_or_create_week(NEXT_MON)  # week+1 — biweekly should SKIP
        weekplan.add_recurring_task("Late biweekly", "Monday", "biweekly", MONDAY)
        data = weekplan.get_or_create_week(NEXT_MON)
        mon = next(d for d in data["days"] if d["name"] == "Monday")
        self.assertNotIn("Late biweekly", [t["text"] for t in mon["items"]])

    def test_forward_fill_no_duplicate_if_already_exists(self):
        # Manually add the task to next week, then trigger forward-fill
        weekplan.get_or_create_week(NEXT_MON)
        weekplan.add_task(NEXT_MON, "Monday", "No dupe")
        # Mark it as recurring so it looks like a regular recurring row
        with weekplan.get_db() as conn:
            conn.execute("UPDATE tasks SET recur='weekly' WHERE text='No dupe' AND week_monday=?", (NEXT_MON,))
        weekplan.add_recurring_task("No dupe", "Monday", "weekly", MONDAY)
        data = weekplan.get_or_create_week(NEXT_MON)
        mon = next(d for d in data["days"] if d["name"] == "Monday")
        count = sum(1 for t in mon["items"] if t["text"] == "No dupe")
        self.assertEqual(count, 1)

    def test_recurring_manager_monthly_day_of_month_anchor(self):
        weekplan.add_recurring_task("Pay rent", "Monday", "monthly", MONDAY, day_of_month=15)
        with weekplan.get_db() as conn:
            row = conn.execute("SELECT created_at FROM tasks WHERE text='Pay rent'").fetchone()
        # created_at should be pinned to the 15th of the current month
        self.assertIn("-15T", row["created_at"])

    def test_list_recurring_returns_correct_fields(self):
        weekplan.add_recurring_task("Track", "Monday", "weekly", MONDAY)
        items = weekplan.list_recurring_tasks()
        item = next(i for i in items if i["text"] == "Track")
        self.assertIn("recur", item)
        self.assertIn("section", item)
        self.assertIn("week_count", item)
        self.assertIn("latest_week", item)


# ── Color ─────────────────────────────────────────────────────────────────────

class TestColor(unittest.TestCase):
    def setUp(self):
        _reset_db()
        weekplan.add_task(MONDAY, "Monday", "Coloured task")

    def test_set_color(self):
        weekplan.set_task_color(MONDAY, "Monday", 0, "red")
        data = weekplan.get_or_create_week(MONDAY)
        mon = next(d for d in data["days"] if d["name"] == "Monday")
        self.assertEqual(mon["items"][0]["color"], "red")

    def test_clear_color(self):
        weekplan.set_task_color(MONDAY, "Monday", 0, "red")
        weekplan.set_task_color(MONDAY, "Monday", 0, "")
        data = weekplan.get_or_create_week(MONDAY)
        mon = next(d for d in data["days"] if d["name"] == "Monday")
        self.assertIsNone(mon["items"][0]["color"])

    def test_set_color_invalid_idx(self):
        result = weekplan.set_task_color(MONDAY, "Monday", 99, "blue")
        self.assertIn("error", result)

    def test_color_carried_with_recurring_task(self):
        # setUp adds "Coloured task" at idx 0; "Coloured recurring" is at idx 1
        weekplan.add_task(MONDAY, "Monday", "Coloured recurring")
        weekplan.set_task_recur(MONDAY, "Monday", 1, "weekly")
        weekplan.set_task_color(MONDAY, "Monday", 1, "purple")
        data = weekplan.get_or_create_week(NEXT_MON)
        mon = next(d for d in data["days"] if d["name"] == "Monday")
        item = next(t for t in mon["items"] if t["text"] == "Coloured recurring")
        self.assertEqual(item["color"], "purple")


# ── Steps ─────────────────────────────────────────────────────────────────────

class TestSteps(unittest.TestCase):
    def setUp(self):
        _reset_db()
        weekplan.add_task(MONDAY, "Monday", "Stepped task")

    def test_set_steps(self):
        weekplan.set_step_count(MONDAY, "Monday", 0, 3)
        data = weekplan.get_or_create_week(MONDAY)
        mon = next(d for d in data["days"] if d["name"] == "Monday")
        self.assertEqual(mon["items"][0]["steps"], [0, 0, 0])

    def test_toggle_step(self):
        weekplan.set_step_count(MONDAY, "Monday", 0, 3)
        weekplan.toggle_step(MONDAY, "Monday", 0, 1, True)
        data = weekplan.get_or_create_week(MONDAY)
        mon = next(d for d in data["days"] if d["name"] == "Monday")
        self.assertEqual(mon["items"][0]["steps"], [0, 1, 0])

    def test_all_steps_done_signals_auto_complete(self):
        weekplan.set_step_count(MONDAY, "Monday", 0, 2)
        weekplan.toggle_step(MONDAY, "Monday", 0, 0, True)
        result = weekplan.toggle_step(MONDAY, "Monday", 0, 1, True)
        self.assertTrue(result.get("auto_complete"))

    def test_reduce_step_count(self):
        weekplan.set_step_count(MONDAY, "Monday", 0, 4)
        weekplan.set_step_count(MONDAY, "Monday", 0, 2)
        data = weekplan.get_or_create_week(MONDAY)
        mon = next(d for d in data["days"] if d["name"] == "Monday")
        self.assertEqual(len(mon["items"][0]["steps"]), 2)

    def test_step_out_of_range(self):
        weekplan.set_step_count(MONDAY, "Monday", 0, 2)
        result = weekplan.toggle_step(MONDAY, "Monday", 0, 5, True)
        self.assertIn("error", result)

    def test_zero_steps_clears(self):
        weekplan.set_step_count(MONDAY, "Monday", 0, 3)
        weekplan.set_step_count(MONDAY, "Monday", 0, 0)
        data = weekplan.get_or_create_week(MONDAY)
        mon = next(d for d in data["days"] if d["name"] == "Monday")
        steps = mon["items"][0]["steps"]
        self.assertFalse(steps)  # None or []

    def test_partial_steps_no_auto_complete(self):
        weekplan.set_step_count(MONDAY, "Monday", 0, 3)
        result = weekplan.toggle_step(MONDAY, "Monday", 0, 0, True)
        self.assertFalse(result.get("auto_complete"))


# ── Bindings ──────────────────────────────────────────────────────────────────

class TestBindings(unittest.TestCase):
    def setUp(self):
        _reset_db()
        weekplan.add_task(MONDAY, "Monday", "Bindable task")

    def test_set_nra_binding(self):
        weekplan.set_task_binding(MONDAY, "Monday", 0, "nra_binding", "project-x")
        data = weekplan.get_or_create_week(MONDAY)
        item = data["days"][0]["items"][0]
        self.assertEqual(item["nra_binding"], "project-x")

    def test_set_dwm_binding(self):
        weekplan.set_task_binding(MONDAY, "Monday", 0, "dwm_binding", "workspace-2")
        data = weekplan.get_or_create_week(MONDAY)
        item = data["days"][0]["items"][0]
        self.assertEqual(item["dwm_binding"], "workspace-2")

    def test_clear_nra_binding(self):
        weekplan.set_task_binding(MONDAY, "Monday", 0, "nra_binding", "project-x")
        weekplan.set_task_binding(MONDAY, "Monday", 0, "nra_binding", "")
        data = weekplan.get_or_create_week(MONDAY)
        item = data["days"][0]["items"][0]
        self.assertIsNone(item["nra_binding"])

    def test_invalid_binding_type(self):
        result = weekplan.set_task_binding(MONDAY, "Monday", 0, "bad_binding", "x")
        self.assertIn("error", result)

    def test_binding_invalid_section(self):
        result = weekplan.set_task_binding(MONDAY, "Funday", 0, "nra_binding", "x")
        self.assertIn("error", result)

    def test_binding_invalid_idx(self):
        result = weekplan.set_task_binding(MONDAY, "Monday", 99, "nra_binding", "x")
        self.assertIn("error", result)

    def test_binding_carried_with_recurring_task(self):
        weekplan.set_task_recur(MONDAY, "Monday", 0, "weekly")
        weekplan.set_task_binding(MONDAY, "Monday", 0, "nra_binding", "my-project")
        data = weekplan.get_or_create_week(NEXT_MON)
        mon = next(d for d in data["days"] if d["name"] == "Monday")
        item = next(t for t in mon["items"] if t["text"] == "Bindable task")
        self.assertEqual(item["nra_binding"], "my-project")


# ── Birthdays ─────────────────────────────────────────────────────────────────

class TestBirthdays(unittest.TestCase):
    def setUp(self):
        _reset_db()

    def test_add_birthday(self):
        result = weekplan.add_birthday("Alice", 6, 15)
        self.assertEqual(result["status"], "saved")

    def test_add_birthday_invalid_month(self):
        result = weekplan.add_birthday("Bob", 13, 1)
        self.assertIn("error", result)

    def test_add_birthday_invalid_day(self):
        result = weekplan.add_birthday("Bob", 6, 32)
        self.assertIn("error", result)

    def test_add_birthday_empty_name(self):
        result = weekplan.add_birthday("  ", 6, 15)
        self.assertIn("error", result)

    def test_birthday_reminder_appears_on_correct_day(self):
        # Birthday June 15 (Sunday of week Jun 9). reminder_offsets="0" → appears June 15
        weekplan.add_birthday("Alice", 6, 15, reminder_offsets="0")
        # MONDAY = 2025-06-02; week containing Jun 15 is Jun 9
        data = weekplan.get_or_create_week("2025-06-09")
        sun = next(d for d in data["days"] if d["name"] == "Sunday")
        texts = [t["text"] for t in sun["items"]]
        self.assertTrue(any("Alice" in t for t in texts))

    def test_birthday_7day_reminder_appears_early(self):
        # Birthday June 15, 7-day offset → reminder June 8 (Sunday of Jun 2 week)
        weekplan.add_birthday("Bob", 6, 15, reminder_offsets="7")
        data = weekplan.get_or_create_week(MONDAY)
        sun = next(d for d in data["days"] if d["name"] == "Sunday")
        texts = [t["text"] for t in sun["items"]]
        self.assertTrue(any("Bob" in t for t in texts))

    def test_birthday_not_in_wrong_week(self):
        weekplan.add_birthday("Charlie", 6, 15, reminder_offsets="0")
        # Week Jun 2 does NOT contain Jun 15
        data = weekplan.get_or_create_week(MONDAY)
        for col in data["days"]:
            for t in col["items"]:
                self.assertNotIn("Charlie", t["text"])

    def test_birthday_reminder_not_duplicated_on_reload(self):
        weekplan.add_birthday("Dedup", 6, 15, reminder_offsets="0")
        weekplan.get_or_create_week("2025-06-09")
        data = weekplan.get_or_create_week("2025-06-09")
        sun = next(d for d in data["days"] if d["name"] == "Sunday")
        count = sum(1 for t in sun["items"] if "Dedup" in t["text"])
        self.assertEqual(count, 1)

    def test_delete_birthday(self):
        weekplan.add_birthday("To delete", 6, 15)
        with weekplan.get_db() as conn:
            bid = conn.execute("SELECT id FROM birthdays WHERE name='To delete'").fetchone()["id"]
        result = weekplan.delete_birthday(bid)
        self.assertEqual(result["status"], "deleted")
        with weekplan.get_db() as conn:
            row = conn.execute("SELECT 1 FROM birthdays WHERE id=?", (bid,)).fetchone()
        self.assertIsNone(row)

    def test_bulk_set_birthday_reminders(self):
        weekplan.add_birthday("A", 1, 1)
        weekplan.add_birthday("B", 2, 2)
        with weekplan.get_db() as conn:
            my_ids = [r["id"] for r in conn.execute(
                "SELECT id FROM birthdays WHERE name IN ('A','B')"
            ).fetchall()]
        weekplan.bulk_set_birthday_reminders(my_ids, "0,14,30")
        with weekplan.get_db() as conn:
            for bid in my_ids:
                row = conn.execute("SELECT reminder_offsets FROM birthdays WHERE id=?", (bid,)).fetchone()
                self.assertEqual(row["reminder_offsets"], "0,14,30")


# ── Section idx consistency ───────────────────────────────────────────────────

class TestSectionIdxConsistency(unittest.TestCase):
    def setUp(self):
        _reset_db()

    def test_section_idx_sequential(self):
        for text in ["A", "B", "C", "D"]:
            weekplan.add_task(MONDAY, "Friday", text)
        data = weekplan.get_or_create_week(MONDAY)
        fri = next(d for d in data["days"] if d["name"] == "Friday")
        for i, item in enumerate(fri["items"]):
            self.assertEqual(item["section_idx"], i)

    def test_section_idx_after_delete(self):
        for text in ["A", "B", "C"]:
            weekplan.add_task(MONDAY, "Thursday", text)
        weekplan.delete_task(MONDAY, "Thursday", 1)
        data = weekplan.get_or_create_week(MONDAY)
        thu = next(d for d in data["days"] if d["name"] == "Thursday")
        self.assertEqual(thu["items"][0]["section_idx"], 0)
        self.assertEqual(thu["items"][1]["section_idx"], 1)

    def test_section_idx_independent_per_day(self):
        for day in ["Monday", "Tuesday"]:
            for text in ["X", "Y"]:
                weekplan.add_task(MONDAY, day, text)
        data = weekplan.get_or_create_week(MONDAY)
        for col in data["days"][:2]:
            self.assertEqual(col["items"][0]["section_idx"], 0)
            self.assertEqual(col["items"][1]["section_idx"], 1)

    def test_section_idx_after_reorder(self):
        for text in ["P", "Q", "R"]:
            weekplan.add_task(MONDAY, "Wednesday", text)
        weekplan.reorder_section(MONDAY, "Wednesday", ["R", "P", "Q"])
        data = weekplan.get_or_create_week(MONDAY)
        wed = next(d for d in data["days"] if d["name"] == "Wednesday")
        for i, item in enumerate(wed["items"]):
            self.assertEqual(item["section_idx"], i)


# ── _sections_for_recur (unit) ────────────────────────────────────────────────

class TestSectionsForRecur(unittest.TestCase):
    def test_daily_returns_all_days(self):
        self.assertEqual(weekplan._sections_for_recur("Monday", "daily"), weekplan.DAY_NAMES)

    def test_weekdays_returns_5_days(self):
        result = weekplan._sections_for_recur("Monday", "weekdays")
        self.assertEqual(len(result), 5)
        self.assertNotIn("Saturday", result)
        self.assertNotIn("Sunday", result)

    def test_specific_day_pattern(self):
        result = weekplan._sections_for_recur("Monday", "every Wed")
        self.assertEqual(result, ["Wednesday"])

    def test_weekly_returns_original_section(self):
        result = weekplan._sections_for_recur("Friday", "weekly")
        self.assertEqual(result, ["Friday"])

    def test_someday_always_returns_someday(self):
        result = weekplan._sections_for_recur("Someday", "daily")
        self.assertEqual(result, ["Someday"])

    def test_no_recur_returns_original(self):
        result = weekplan._sections_for_recur("Tuesday", "")
        self.assertEqual(result, ["Tuesday"])


# ── _month_interval (unit) ────────────────────────────────────────────────────

class TestMonthInterval(unittest.TestCase):
    def test_monthly(self):
        self.assertEqual(weekplan._month_interval("monthly"), 1)

    def test_annually(self):
        self.assertEqual(weekplan._month_interval("annually"), 12)

    def test_every_3_months(self):
        self.assertEqual(weekplan._month_interval("every 3 months"), 3)

    def test_every_2_years(self):
        self.assertEqual(weekplan._month_interval("every 2 years"), 24)

    def test_weekly_returns_none(self):
        self.assertIsNone(weekplan._month_interval("weekly"))

    def test_daily_returns_none(self):
        self.assertIsNone(weekplan._month_interval("daily"))

    def test_case_insensitive(self):
        self.assertEqual(weekplan._month_interval("Monthly"), 1)


# ── _add_months (unit) ────────────────────────────────────────────────────────

class TestAddMonths(unittest.TestCase):
    def test_simple(self):
        self.assertEqual(weekplan._add_months(date(2025, 1, 15), 1), date(2025, 2, 15))

    def test_year_rollover(self):
        self.assertEqual(weekplan._add_months(date(2025, 11, 15), 3), date(2026, 2, 15))

    def test_clamps_end_of_month(self):
        # Jan 31 + 1 month → Feb 28
        self.assertEqual(weekplan._add_months(date(2025, 1, 31), 1), date(2025, 2, 28))

    def test_leap_year(self):
        # Jan 31 + 1 month in leap year → Feb 29
        self.assertEqual(weekplan._add_months(date(2024, 1, 31), 1), date(2024, 2, 29))

    def test_12_months_same_day(self):
        self.assertEqual(weekplan._add_months(date(2025, 6, 15), 12), date(2026, 6, 15))


if __name__ == "__main__":
    unittest.main()
