import unittest
from unittest.mock import ANY, Mock, patch

from app.jobs import run_pipeline


class RunPipelineTests(unittest.TestCase):
    def test_cleaning_receives_ingestion_run_id(self):
        storage = Mock()
        storage.save_run_report.return_value = "memory://run-report.log"

        def ingestion_result(**kwargs):
            return {"run_id": kwargs["run_id"], "status": "complete"}

        def cleaning_result(run_id, storage=None):
            return {"run_id": run_id, "status": "complete"}

        with (
            patch.object(
                run_pipeline,
                "run_ingestion",
                side_effect=ingestion_result,
            ) as ingestion,
            patch.object(
                run_pipeline,
                "run_cleaning",
                side_effect=cleaning_result,
            ) as cleaner,
        ):
            result = run_pipeline.run(storage=storage)

        ingestion.assert_called_once_with(
            storage=storage,
            started_at=ANY,
            run_id=ANY,
        )
        assigned_run_id = ingestion.call_args.kwargs["run_id"]
        cleaner.assert_called_once_with(assigned_run_id, storage=storage)
        self.assertEqual(result["status"], "complete")
        storage.save_run_report.assert_called_once()
        saved_run_id, report = storage.save_run_report.call_args.args
        self.assertEqual(saved_run_id, assigned_run_id)
        self.assertIn("RUN SUMMARY", report)
        self.assertIn('"total_duration_seconds"', report)

    def test_failure_is_logged_and_report_is_still_saved(self):
        storage = Mock()
        storage.save_run_report.return_value = "memory://run-report.log"
        with (
            patch.object(
                run_pipeline,
                "run_ingestion",
                side_effect=RuntimeError("source exploded"),
            ),
            self.assertRaisesRegex(RuntimeError, "source exploded"),
        ):
            run_pipeline.run(storage=storage)

        storage.save_run_report.assert_called_once()
        report = storage.save_run_report.call_args.args[1]
        self.assertIn("source exploded", report)
        self.assertIn('"status": "failed"', report)


if __name__ == "__main__":
    unittest.main()
