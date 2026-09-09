import unittest
from unittest.mock import patch

from app.ingestion.source_registry import WORDPRESS_SOURCES
from app.jobs import run_ingestion


class Response:
    def json(self):
        return {"occurrences": []}


class WordPressClient:
    def get_all(
        self,
        endpoint,
        fields,
        include=None,
        modified_after=None,
    ):
        return [{"id": 1}]

    def search_all(self, subtype):
        return [{"id": 1, "title": "Event", "url": "https://example.test/events/one/"}]


class FailingWordPressClient:
    def get_all(
        self,
        endpoint,
        fields,
        include=None,
        modified_after=None,
    ):
        raise RuntimeError("source unavailable")

    def search_all(self, subtype):
        raise RuntimeError("source unavailable")


class MecClient:
    def get_event_details(self, search_results):
        return {
            "events": [{
                "search_result": search_results[0],
                "response": {
                    "data": {
                        "ID": "1",
                        "title": "Event",
                        "content": "Complete event content",
                        "permalink": search_results[0]["url"],
                        "post": {},
                    },
                    "dates": [],
                },
            }],
            "failures": [],
        }

    def get_next_occurrences(self, event_id):
        return Response()


class PMProClient:
    def get_membership_levels(self):
        return [{"id": 1}]


class EmptyPMProClient(PMProClient):
    def get_membership_levels(self):
        return []


class HtmlCrawler:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.__class__.instances.append(self)

    def crawl(self, source_name, config, **kwargs):
        return [{"id": 1}], {
            "discovered": 1,
            "fetched": 1,
            "accepted": 1,
            "skipped": 0,
            "failed": 0,
            "limit_reached": False,
        }


class EmptyHtmlCrawler(HtmlCrawler):
    def crawl(self, source_name, config, **kwargs):
        return [], {
            "discovered": 1,
            "fetched": 1,
            "accepted": 0,
            "skipped": 1,
            "failed": 0,
            "limit_reached": False,
        }


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
        self.run_sources = {}
        self.manifests = []
        self.raw_latest = None

    def save_source(self, run_id, source_name, data):
        self.sources[source_name] = data
        self.run_sources[(run_id, source_name)] = data
        return f"memory://{run_id}/{source_name}.json"

    def save_manifest(self, run_id, manifest):
        self.manifests.append(manifest)
        return f"memory://{run_id}/manifest.json"

    def load_manifest(self, run_id):
        return next(
            manifest for manifest in reversed(self.manifests)
            if manifest["run_id"] == run_id
        )

    def load_source(self, run_id, source_name):
        return self.run_sources[(run_id, source_name)]

    def publish_raw_latest(self, run_id, manifest_path, status=None):
        self.raw_latest = {
            "run_id": run_id,
            "manifest": manifest_path,
            "status": status,
        }
        return "memory://latest.json"

    def load_raw_latest(self):
        return self.raw_latest


class IngestionJobTests(unittest.TestCase):
    def run_with_wordpress(
        self,
        wordpress_client,
        crawler_class=HtmlCrawler,
        pmpro_client=None,
    ):
        crawler_class.instances = []
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
            patch.object(
                run_ingestion,
                "PMProClient",
                return_value=pmpro_client or PMProClient(),
            ),
            patch.object(run_ingestion, "HtmlCrawler", crawler_class),
            patch.object(run_ingestion, "MAX_CALENDAR_EVENTS", None),
        ]
        for active_patch in patches:
            active_patch.start()
            self.addCleanup(active_patch.stop)
        return storage

    def test_empty_membership_plans_fail_automated_quality_gate(self):
        self.run_with_wordpress(
            WordPressClient(),
            pmpro_client=EmptyPMProClient(),
        )

        manifest = run_ingestion.run()

        self.assertEqual(manifest["status"], "complete_with_errors")
        self.assertFalse(manifest["quality_gate"]["passed"])
        self.assertIn(
            {
                "source": "membership_plans",
                "minimum": 1,
                "actual": 0,
            },
            manifest["quality_gate"]["violations"],
        )

    def test_external_faq_crawler_never_receives_wordpress_credentials(self):
        self.run_with_wordpress(WordPressClient())

        run_ingestion.run()

        faq_crawler = next(
            crawler for crawler in HtmlCrawler.instances
            if crawler.kwargs["base_url"]
            == "https://atlas.culturalinfusion.com"
        )
        membership_crawler = next(
            crawler for crawler in HtmlCrawler.instances
            if crawler.kwargs["base_url"] == run_ingestion.WORDPRESS_BASE_URL
        )
        self.assertIsNone(faq_crawler.kwargs["auth"])
        self.assertEqual(
            membership_crawler.kwargs["auth"],
            (
                run_ingestion.WORDPRESS_USERNAME,
                run_ingestion.WORDPRESS_APP_PASSWORD,
            ),
        )

    def test_success_writes_complete_manifest(self):
        storage = self.run_with_wordpress(WordPressClient())

        manifest = run_ingestion.run()

        self.assertEqual(manifest["status"], "complete")
        self.assertGreaterEqual(manifest["duration_seconds"], 0)
        self.assertEqual(
            set(WORDPRESS_SOURCES).union({"membership_plans", "calendar_occurrences"}),
            set(storage.sources),
        )
        self.assertEqual(storage.manifests[-1], manifest)

    def test_second_successful_run_uses_incremental_baseline(self):
        self.run_with_wordpress(WordPressClient())

        first = run_ingestion.run()
        second = run_ingestion.run()

        self.assertEqual(first["incremental"]["mode"], "full")
        self.assertEqual(second["incremental"]["mode"], "incremental")
        self.assertEqual(
            second["incremental"]["previous_run_id"],
            first["run_id"],
        )
        self.assertTrue(second["baseline_eligible"])

    def test_source_errors_are_isolated_and_recorded(self):
        self.run_with_wordpress(FailingWordPressClient())

        manifest = run_ingestion.run()

        self.assertEqual(manifest["status"], "complete_with_errors")
        self.assertEqual(manifest["source_failures"], [])
        self.assertGreater(manifest["api_fallback_count"], 0)
        fallback_summaries = [
            summary for summary in manifest["source_fetches"].values()
            if summary.get("api_fallback")
        ]
        self.assertEqual(
            len(fallback_summaries), manifest["api_fallback_count"]
        )

    def test_empty_expected_html_source_is_complete_with_errors(self):
        self.run_with_wordpress(WordPressClient(), crawler_class=EmptyHtmlCrawler)

        manifest = run_ingestion.run()

        self.assertEqual(manifest["status"], "complete_with_errors")
        self.assertEqual(manifest["html_crawls"]["info_hub"]["accepted"], 0)


if __name__ == "__main__":
    unittest.main()
