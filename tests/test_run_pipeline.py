import unittest
from unittest.mock import patch

from app.jobs import run_pipeline


class RunPipelineTests(unittest.TestCase):
    def test_cleaning_receives_ingestion_run_id(self):
        with (
            patch.object(
                run_pipeline,
                "run_ingestion",
                return_value={"run_id": "run-123", "status": "complete"},
            ),
            patch.object(
                run_pipeline,
                "run_cleaning",
                return_value={"run_id": "run-123", "status": "complete"},
            ) as cleaner,
        ):
            result = run_pipeline.run()

        cleaner.assert_called_once_with("run-123")
        self.assertEqual(result["status"], "complete")


if __name__ == "__main__":
    unittest.main()
