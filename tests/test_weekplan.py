"""Tests for daemons/weekplan.py — uses a temp DB, patches config paths."""

import json
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
        texts = [t["text"] for t in mon["items"]]
        self.assertEqual(texts, ["First", "Second", "Third"])

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


class TestDelete(unittest.TestCase):
    def setUp(self):
        _reset_db()
        weekplan.add_task(MONDAY, "Monday", "Alpha")
        weekplan.add_task(MONDAY, "Monday", "Beta")
        weekplan.add_task(MONDAY, "Monday", "Gamma")

    def test_delete_middle(self):
        weekplan.delete_task(MONDAY, "Monday", 1)  # delete Beta
        data = weekplan.get_or_create_week(MONDAY)
        mon = next(d for d in data["days"] if d["name"] == "Monday")
        texts = [t["text"] for t in mon["items"]]
        self.assertEqual(texts, ["Alpha", "Gamma"])

    def test_delete_renormalises_indices(self):
        weekplan.delete_task(MONDAY, "Monday", 0)  # delete Alpha
        data = weekplan.get_or_create_week(MONDAY)
        mon = next(d for d in data["days"] if d["name"] == "Monday")
        self.assertEqual(mon["items"][0]["section_idx"], 0)
        self.assertEqual(mon["items"][1]["section_idx"], 1)

    def test_delete_invalid_idx(self):
        result = weekplan.delete_task(MONDAY, "Monday", 99)
        self.assertIn("error", result)


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
        texts = [t["text"] for t in mon["items"]]
        self.assertEqual(texts, ["Gamma", "Alpha", "Beta"])

    def test_reorder_partial_list(self):
        """Items omitted from ordered list keep their relative positions."""
        weekplan.reorder_section(MONDAY, "Monday", ["Beta"])
        data = weekplan.get_or_create_week(MONDAY)
        mon = next(d for d in data["days"] if d["name"] == "Monday")
        # Beta gets position 0; Alpha and Gamma get renormalized after
        texts = [t["text"] for t in mon["items"]]
        self.assertIn("Beta", texts)

    def test_reorder_invalid_section(self):
        result = weekplan.reorder_section(MONDAY, "Funday", ["Alpha"])
        self.assertIn("error", result)

    def test_reorder_someday(self):
        weekplan.add_task(MONDAY, "Someday", "S1")
        weekplan.add_task(MONDAY, "Someday", "S2")
        weekplan.add_task(MONDAY, "Someday", "S3")
        weekplan.reorder_section(MONDAY, "Someday", ["S3", "S1", "S2"])
        data = weekplan.get_or_create_week(MONDAY)
        texts = [t["text"] for t in data["someday"]]
        self.assertEqual(texts, ["S3", "S1", "S2"])


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
        self.assertEqual(len(tue["items"]), 1)
        self.assertEqual(tue["items"][0]["text"], "Defer me")

    def test_defer_to_someday(self):
        weekplan.defer_task(MONDAY, "Monday", 0, "someday")
        data = weekplan.get_or_create_week(MONDAY)
        self.assertEqual(len(data["someday"]), 1)

    def test_defer_to_next_week(self):
        weekplan.defer_task(MONDAY, "Monday", 0, "next_week")
        next_mon = (date.fromisoformat(MONDAY) + timedelta(days=7)).isoformat()
        data = weekplan.get_or_create_week(next_mon)
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


class TestDuplicate(unittest.TestCase):
    def setUp(self):
        _reset_db()
        weekplan.add_task(MONDAY, "Monday", "Original")

    def test_duplicate_creates_copy(self):
        weekplan.duplicate_task(MONDAY, "Monday", 0)
        data = weekplan.get_or_create_week(MONDAY)
        mon = next(d for d in data["days"] if d["name"] == "Monday")
        texts = [t["text"] for t in mon["items"]]
        self.assertEqual(texts, ["Original", "Original"])

    def test_duplicate_preserves_note(self):
        weekplan.set_task_note(MONDAY, "Monday", 0, "keep this")
        weekplan.duplicate_task(MONDAY, "Monday", 0)
        data = weekplan.get_or_create_week(MONDAY)
        mon = next(d for d in data["days"] if d["name"] == "Monday")
        self.assertEqual(mon["items"][1]["note"], "keep this")


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
            texts = [t["text"] for t in col["items"]]
            self.assertIn("Daily task", texts, f"Missing from {day}")

    def test_clear_recur(self):
        weekplan.set_task_recur(MONDAY, "Monday", 0, "daily")
        # Reload to find updated index (daily expanded to all days)
        weekplan.set_task_recur(MONDAY, "Monday", 0, "")
        data = weekplan.get_or_create_week(MONDAY)
        mon = next(d for d in data["days"] if d["name"] == "Monday")
        self.assertIsNone(mon["items"][0]["recur"])


class TestCarryForward(unittest.TestCase):
    def setUp(self):
        _reset_db()

    def test_unchecked_recurring_carried(self):
        weekplan.add_task(MONDAY, "Monday", "Weekly review")
        weekplan.set_task_recur(MONDAY, "Monday", 0, "weekly")

        next_mon = (date.fromisoformat(MONDAY) + timedelta(days=7)).isoformat()
        data = weekplan.get_or_create_week(next_mon)
        mon = next(d for d in data["days"] if d["name"] == "Monday")
        texts = [t["text"] for t in mon["items"]]
        self.assertIn("Weekly review", texts)

    def test_checked_recurring_not_carried(self):
        weekplan.add_task(MONDAY, "Monday", "Done task")
        weekplan.set_task_recur(MONDAY, "Monday", 0, "weekly")
        weekplan.toggle_task(MONDAY, "Monday", 0, True)

        next_mon = (date.fromisoformat(MONDAY) + timedelta(days=7)).isoformat()
        data = weekplan.get_or_create_week(next_mon)
        mon = next(d for d in data["days"] if d["name"] == "Monday")
        texts = [t["text"] for t in mon["items"]]
        self.assertNotIn("Done task", texts)

    def test_someday_tasks_carried_to_new_week(self):
        weekplan.add_task(MONDAY, "Someday", "Persistent idea")
        next_mon = (date.fromisoformat(MONDAY) + timedelta(days=7)).isoformat()
        data = weekplan.get_or_create_week(next_mon)
        texts = [t["text"] for t in data["someday"]]
        self.assertIn("Persistent idea", texts)

    def test_biweekly_skips_alternate_weeks(self):
        weekplan.add_task(MONDAY, "Monday", "Bi-weekly")
        weekplan.set_task_recur(MONDAY, "Monday", 0, "biweekly")

        week2 = (date.fromisoformat(MONDAY) + timedelta(days=7)).isoformat()
        week3 = (date.fromisoformat(MONDAY) + timedelta(days=14)).isoformat()

        data2 = weekplan.get_or_create_week(week2)
        mon2 = next(d for d in data2["days"] if d["name"] == "Monday")
        texts2 = [t["text"] for t in mon2["items"]]
        self.assertNotIn("Bi-weekly", texts2, "Should skip week 2")

        data3 = weekplan.get_or_create_week(week3)
        mon3 = next(d for d in data3["days"] if d["name"] == "Monday")
        texts3 = [t["text"] for t in mon3["items"]]
        self.assertIn("Bi-weekly", texts3, "Should appear week 3")


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


class TestSectionIdxConsistency(unittest.TestCase):
    """section_idx must be the 0-based position within the section."""

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
        weekplan.delete_task(MONDAY, "Thursday", 1)  # remove B
        data = weekplan.get_or_create_week(MONDAY)
        thu = next(d for d in data["days"] if d["name"] == "Thursday")
        self.assertEqual(thu["items"][0]["section_idx"], 0)
        self.assertEqual(thu["items"][1]["section_idx"], 1)


if __name__ == "__main__":
    unittest.main()
