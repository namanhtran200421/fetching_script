import unittest
from unittest.mock import patch

from app.ingestion.source_registry import WORDPRESS_SOURCES
from app.jobs import run_ingestion


class Response:
    def json(self):
        return {"occurrences": []}


class WordPressClient:
    def get_all(self, endpoint, fields):
        return [{"id": 1}]


class FailingWordPressClient:
    def get_all(self, endpoint, fields):
        raise RuntimeError("source unavailable")


class MecClient:
    def get_next_occurrences(self, event_id):
        return Response()


class PMProClient:
    def get_membership_levels(self):
        return [{"id": 1}]


class HttpContext:
    def __init__(self, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        pass


class MemoryStorage:
    def __init__(self):
        self.sources = {}
        self.manifests = []

    def save_source(self, run_id, source_name, data):
        self.sources[source_name] = data
        return f"memory://{run_id}/{source_name}.json"

    def save_manifest(self, run_id, manifest):
        self.manifests.append(manifest)
        return f"memory://{run_id}/manifest.json"


class IngestionJobTests(unittest.TestCase):
    def run_with_wordpress(self, wordpress_client):
        storage = MemoryStorage()
        patches = [
            patch.object(run_ingestion, "validate_config"),
            patch.object(run_ingestion, "build_storage", return_value=storage),
            patch.object(run_ingestion, "RetryingHttpClient", HttpContext),
            patch.object(
                run_ingestion,
                "WordPressClient",
                return_value=wordpress_client,
            ),
            patch.object(run_ingestion, "MecCalendarClient", return_value=MecClient()),
            patch.object(run_ingestion, "PMProClient", return_value=PMProClient()),
            patch.object(run_ingestion, "MAX_CALENDAR_EVENTS", None),
        ]
        for active_patch in patches:
            active_patch.start()
            self.addCleanup(active_patch.stop)
        return storage

    def test_success_writes_complete_manifest(self):
        storage = self.run_with_wordpress(WordPressClient())

        manifest = run_ingestion.run()

        self.assertEqual(manifest["status"], "complete")
        self.assertEqual(
            set(WORDPRESS_SOURCES).union({"membership_plans", "calendar_occurrences"}),
            set(storage.sources),
        )
        self.assertEqual(storage.manifests[-1], manifest)

    def test_fatal_error_writes_failure_manifest_and_reraises(self):
        storage = self.run_with_wordpress(FailingWordPressClient())

        with self.assertLogs(run_ingestion.LOGGER, level="ERROR"):
            with self.assertRaisesRegex(RuntimeError, "source unavailable"):
                run_ingestion.run()

        self.assertEqual(storage.manifests[-1]["status"], "failed")
        self.assertEqual(storage.manifests[-1]["error"]["type"], "RuntimeError")


if __name__ == "__main__":
    unittest.main()
