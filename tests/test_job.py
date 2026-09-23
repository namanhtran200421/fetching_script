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
        extra_params=None,
    ):
        return [{"id": 1, "link": "https://example.test/one/"}]

    def get_terms(self, taxonomy_endpoint, fields=None):
        return [{"id": 1, "name": "Insights", "slug": "insights"}]

    def search_all(self, subtype):
        return [{"id": 1, "title": "Event", "url": "https://example.test/events/one/"}]


class FailingWordPressClient:
    def get_all(
        self,
        endpoint,
        fields,
        include=None,
        modified_after=None,
        extra_params=None,
    ):
        raise RuntimeError("source unavailable")

    def get_terms(self, taxonomy_endpoint, fields=None):
        raise RuntimeError("source unavailable")

    def search_all(self, subtype):
        raise RuntimeError("source unavailable")


class MediaClient:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.__class__.instances.append(self)

    def list_documents(self, fields=None, modified_after=None):
        return [{
            "id": 1,
            "mime_type": "application/pdf",
            "source_url": "https://atlas.culturalinfusion.com/a.pdf",
            "modified_gmt": "2026-09-01T00:00:00",
        }]

    def read_documents(self, attachments):
        return {
            "documents": [
                {**item, "content_text": "Report text", "page_count": 2}
                for item in attachments
            ],
            "failures": [],
        }


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
    def _record_wordpress_client(self, wordpress_client):
        """Return a factory that captures how each client was constructed."""
        def build(**kwargs):
            self.wordpress_client_kwargs.append(kwargs)
            return wordpress_client
        return build

    def run_with_wordpress(
        self,
        wordpress_client,
        crawler_class=HtmlCrawler,
        pmpro_client=None,
    ):
        crawler_class.instances = []
        MediaClient.instances = []
        self.wordpress_client_kwargs = []
        storage = MemoryStorage()
        patches = [
            patch.object(run_ingestion, "validate_config"),
            patch.object(run_ingestion, "build_storage", return_value=storage),
            patch.object(run_ingestion, "RetryingHttpClient", HttpContext),
            patch.object(
                run_ingestion,
                "WordPressClient",
                side_effect=self._record_wordpress_client(wordpress_client),
            ),
            patch.object(run_ingestion, "MediaClient", MediaClient),
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

    def test_public_rest_and_media_clients_are_built_without_credentials(self):
        self.run_with_wordpress(WordPressClient())

        run_ingestion.run()

        public = [
            kwargs for kwargs in self.wordpress_client_kwargs
            if kwargs["base_url"] != run_ingestion.WORDPRESS_BASE_URL
        ]
        membership = [
            kwargs for kwargs in self.wordpress_client_kwargs
            if kwargs["base_url"] == run_ingestion.WORDPRESS_BASE_URL
        ]

        self.assertTrue(public)
        for kwargs in public:
            self.assertIsNone(kwargs["username"])
            self.assertIsNone(kwargs["application_password"])
        # One client per external origin, not one per source.
        self.assertEqual(
            len({kwargs["base_url"] for kwargs in public}), len(public)
        )
        self.assertEqual(len(membership), 1)
        self.assertEqual(
            membership[0]["username"], run_ingestion.WORDPRESS_USERNAME
        )

        # The media client borrows that unauthenticated client for listing.
        media_client = MediaClient.instances[0]
        self.assertNotEqual(
            media_client.kwargs["base_url"], run_ingestion.WORDPRESS_BASE_URL
        )

    def test_public_atlas_sources_are_registered_as_public(self):
        public_sources = {
            name: config for name, config in WORDPRESS_SOURCES.items()
            if config.get("base_url")
        }

        self.assertIn("atlas_posts", public_sources)
        for name, config in public_sources.items():
            with self.subTest(source=name):
                self.assertEqual(config["access_class"], "public")
                # One shared crawler caches a single sitemap index per
                # origin, so the sources on it must agree on its path.
                if config.get("sitemap_path"):
                    self.assertEqual(
                        config["sitemap_path"], "/sitemap_index.xml"
                    )

        # The public site's events are teaser stubs whose full text is
        # already ingested from the membership site.
        self.assertNotIn("atlas_mec_events", WORDPRESS_SOURCES)

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
