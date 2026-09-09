import unittest
from datetime import datetime, timezone

from app.ingestion.incremental import build_incremental_plan


class IncrementalPlanTests(unittest.TestCase):
    def setUp(self):
        self.started_at = datetime(2026, 9, 9, tzinfo=timezone.utc)

    def test_first_run_is_a_full_reconciliation(self):
        plan = build_incremental_plan(self.started_at, None)

        self.assertEqual(plan["mode"], "full")
        self.assertTrue(plan["full_reconciliation"])
        self.assertEqual(plan["reason"], "no complete baseline")

    def test_recent_baseline_uses_overlap_and_incremental_mode(self):
        plan = build_incremental_plan(
            self.started_at,
            {
                "run_id": "previous",
                "completed_at": "2026-09-08T12:00:00+00:00",
                "last_full_reconciliation_at": (
                    "2026-09-05T00:00:00+00:00"
                ),
            },
            lookback_seconds=300,
        )

        self.assertEqual(plan["mode"], "incremental")
        self.assertFalse(plan["full_reconciliation"])
        self.assertEqual(
            plan["modified_after"],
            "2026-09-08T11:55:00+00:00",
        )

    def test_weekly_reconciliation_catches_deletions(self):
        plan = build_incremental_plan(
            self.started_at,
            {
                "run_id": "previous",
                "completed_at": "2026-09-08T12:00:00+00:00",
                "last_full_reconciliation_at": (
                    "2026-09-01T00:00:00+00:00"
                ),
            },
            reconciliation_interval_days=7,
        )

        self.assertEqual(plan["mode"], "full")
        self.assertEqual(
            plan["reason"],
            "scheduled full reconciliation is due",
        )


if __name__ == "__main__":
    unittest.main()
