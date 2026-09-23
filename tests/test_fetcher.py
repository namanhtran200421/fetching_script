import unittest

from app.ingestion.fetcher import IngestionFetcher


class Response:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class MecClient:
    def get_next_occurrences(self, event_id):
        if event_id == 2:
            raise RuntimeError("temporary test failure")
        return Response({"event": event_id})

    def get_event_details(self, search_results):
        return {
            "events": [{
                "search_result": search_results[0],
                "response": {
                    "data": {
                        "ID": str(search_results[0]["id"]),
                        "title": "Calendar event",
                        "content": "Full calendar event description",
                        "permalink": "https://example.test/events/one/",
                        "post": {"post_modified_gmt": "2026-09-01 00:00:00"},
                    },
                    "dates": [{
                        "start": {"date": "2026-09-10"},
                        "end": {"date": "2026-09-10"},
                    }],
                },
            }],
            "failures": [],
        }


class WordPressClient:
    def __init__(self):
        self.calls = []

    def get_all(self, **kwargs):
        self.calls.append(kwargs)
        return [{"id": 1}]

    def search_all(self, subtype):
        self.calls.append({"search_subtype": subtype})
        return [{"id": 10, "title": "Event", "url": "https://example.test/events/one/"}]


class FetcherTests(unittest.TestCase):
    def test_external_html_source_uses_its_dedicated_crawler(self):
        class RecordingCrawler:
            def __init__(self, marker):
                self.marker = marker

            def crawl(self, source_name, source, **kwargs):
                return [{"id": self.marker}], {
                    "accepted": 1,
                    "failed": 0,
                }

        fetcher = IngestionFetcher(
            WordPressClient(),
            MecClient(),
            html_crawler=RecordingCrawler("membership"),
            html_crawlers={"faq": RecordingCrawler("atlas")},
        )

        self.assertEqual(fetcher.fetch_source("faq"), [{"id": "atlas"}])

    def test_incremental_rest_fetch_merges_changes_into_complete_snapshot(self):
        class DeltaWordPressClient(WordPressClient):
            def get_all(self, **kwargs):
                self.calls.append(kwargs)
                return [
                    {"id": 2, "title": "Updated"},
                    {"id": 3, "title": "New"},
                ]

        wordpress = DeltaWordPressClient()
        fetcher = IngestionFetcher(wordpress, MecClient())

        records = fetcher.fetch_source(
            "research",
            previous_records=[
                {"id": 1, "title": "Unchanged"},
                {"id": 2, "title": "Old"},
            ],
            modified_after="2026-09-01T00:00:00+00:00",
            incremental=True,
        )

        self.assertEqual([record["id"] for record in records], [1, 2, 3])
        self.assertEqual(records[1]["title"], "Updated")
        self.assertEqual(
            wordpress.calls[0]["modified_after"],
            "2026-09-01T00:00:00+00:00",
        )
        self.assertEqual(fetcher.source_summaries["research"]["changed"], 2)
        self.assertEqual(fetcher.source_summaries["research"]["reused"], 1)

    def test_rest_ingestion_is_preserved_and_pages_use_explicit_allowlist(self):
        wordpress = WordPressClient()
        fetcher = IngestionFetcher(
            wordpress, MecClient(), wordpress_page_allowlist=(12, 34)
        )

        self.assertEqual(fetcher.fetch_source("research"), [{"id": 1}])
        self.assertEqual(fetcher.fetch_source("pages"), [{"id": 1}])
        self.assertIsNone(wordpress.calls[0]["include"])
        self.assertEqual(wordpress.calls[1]["include"], (12, 34))

    def test_empty_page_allowlist_fetches_all_published_pages(self):
        wordpress = WordPressClient()
        fetcher = IngestionFetcher(wordpress, MecClient())

        self.assertEqual(fetcher.fetch_source("pages"), [{"id": 1}])
        self.assertIsNone(wordpress.calls[0]["include"])

    def test_mec_inventory_uses_search_and_complete_event_api(self):
        wordpress = WordPressClient()
        fetcher = IngestionFetcher(wordpress, MecClient())

        records = fetcher.fetch_source("mec_event_definitions")
        occurrences = fetcher.fetch_calendar_occurrences(records)

        self.assertEqual(records[0]["id"], 10)
        self.assertEqual(records[0]["acquisition_method"], "mec_api")
        self.assertEqual(
            occurrences["events"][0]["response"]["derived_from"],
            "mec_event_details",
        )
        self.assertEqual(
            fetcher.source_summaries["mec_event_definitions"]["failed"], 0
        )

    def test_calendar_records_successes_and_failures(self):
        fetcher = IngestionFetcher(None, MecClient())

        result = fetcher.fetch_calendar_occurrences([
            {"id": 1},
            {"id": 2},
            {"title": "missing id"},
        ])

        self.assertEqual(len(result["events"]), 1)
        self.assertEqual(len(result["failures"]), 2)

    def test_calendar_limit_is_optional(self):
        fetcher = IngestionFetcher(None, MecClient())

        result = fetcher.fetch_calendar_occurrences(
            [{"id": 1}, {"id": 3}],
            max_events=1,
        )

        self.assertEqual([item["event_id"] for item in result["events"]], [1])


class PublicAtlasSourceTests(unittest.TestCase):
    """The public site must never be fetched with membership credentials."""

    def test_public_rest_source_uses_its_own_unauthenticated_client(self):
        class PublicWordPressClient(WordPressClient):
            def get_all(self, **kwargs):
                self.calls.append(kwargs)
                return [{"id": 9, "link": "https://atlas.test/insight/"}]

        membership = WordPressClient()
        public = PublicWordPressClient()
        fetcher = IngestionFetcher(
            membership,
            MecClient(),
            wordpress_clients={"atlas_posts": public},
        )

        records = fetcher.fetch_source("atlas_posts")

        self.assertEqual(records, [{"id": 9, "link": "https://atlas.test/insight/"}])
        self.assertEqual(len(public.calls), 1)
        self.assertEqual(membership.calls, [])

    def test_membership_sources_keep_using_the_default_client(self):
        membership = WordPressClient()
        public = WordPressClient()
        fetcher = IngestionFetcher(
            membership,
            MecClient(),
            wordpress_clients={"atlas_posts": public},
        )

        fetcher.fetch_source("research")

        self.assertEqual(len(membership.calls), 1)
        self.assertEqual(public.calls, [])

    def test_denylisted_page_slugs_are_excluded_and_counted_as_skipped(self):
        class PageClient(WordPressClient):
            def get_all(self, **kwargs):
                self.calls.append(kwargs)
                return [
                    {"id": 1, "link": "https://atlas.test/about/"},
                    {"id": 2, "link": "https://atlas.test/faq/"},
                    {"id": 3, "link": "https://atlas.test/security-quiz/"},
                ]

        fetcher = IngestionFetcher(
            WordPressClient(),
            MecClient(),
            wordpress_clients={"atlas_pages": PageClient()},
            slug_denylists={"ATLAS_PAGE_DENYLIST": ("faq", "security-quiz")},
        )

        records = fetcher.fetch_source("atlas_pages")

        self.assertEqual([record["id"] for record in records], [1])
        summary = fetcher.source_summaries["atlas_pages"]
        self.assertEqual(summary["accepted"], 1)
        self.assertEqual(summary["skipped"], 2)

    def test_denylist_also_filters_the_html_fallback_path(self):
        class BrokenClient(WordPressClient):
            def get_all(self, **kwargs):
                raise RuntimeError("REST unavailable")

        class FallbackCrawler:
            def crawl(self, source_name, source, **kwargs):
                return [
                    {"id": 1, "link": "https://atlas.test/about/"},
                    {"id": 2, "link": "https://atlas.test/faq/"},
                ], {"accepted": 2, "skipped": 0, "failed": 0}

        fetcher = IngestionFetcher(
            WordPressClient(),
            MecClient(),
            wordpress_clients={"atlas_pages": BrokenClient()},
            html_crawlers={"atlas_pages": FallbackCrawler()},
            slug_denylists={"ATLAS_PAGE_DENYLIST": ("faq",)},
        )

        records = fetcher.fetch_source("atlas_pages")

        self.assertEqual([record["id"] for record in records], [1])
        self.assertEqual(fetcher.source_summaries["atlas_pages"]["skipped"], 1)

    def test_taxonomy_source_tags_each_term_and_survives_one_failure(self):
        class TermClient:
            def get_terms(self, taxonomy_endpoint, fields=None):
                if taxonomy_endpoint == "tags":
                    raise RuntimeError("taxonomy unavailable")
                return [{"id": 1, "name": taxonomy_endpoint.title()}]

        fetcher = IngestionFetcher(
            WordPressClient(),
            MecClient(),
            wordpress_clients={"atlas_taxonomies": TermClient()},
        )

        records = fetcher.fetch_source("atlas_taxonomies")

        self.assertEqual(
            sorted(record["taxonomy"] for record in records),
            ["category", "mec_category"],
        )
        summary = fetcher.source_summaries["atlas_taxonomies"]
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(summary["failure_details"][0]["taxonomy"], "post_tag")


class MediaSourceTests(unittest.TestCase):
    class RecordingMediaClient:
        def __init__(self, attachments, failures=()):
            self.attachments = attachments
            self.failures = list(failures)
            self.read_calls = []

        def list_documents(self, fields=None, modified_after=None):
            return list(self.attachments)

        def read_documents(self, attachments):
            self.read_calls.append([item["id"] for item in attachments])
            return {
                "documents": [
                    {**item, "content_text": f"text for {item['id']}"}
                    for item in attachments
                ],
                "failures": self.failures,
            }

    def _fetcher(self, media_client):
        return IngestionFetcher(
            WordPressClient(),
            MecClient(),
            media_clients={"atlas_media_documents": media_client},
        )

    def test_full_run_reads_every_attachment(self):
        media = self.RecordingMediaClient([
            {"id": 1, "modified_gmt": "2026-01-01T00:00:00"},
            {"id": 2, "modified_gmt": "2026-01-02T00:00:00"},
        ])

        records = self._fetcher(media).fetch_source("atlas_media_documents")

        self.assertEqual(media.read_calls, [[1, 2]])
        self.assertEqual(
            [record["content_text"] for record in records],
            ["text for 1", "text for 2"],
        )

    def test_incremental_run_reuses_unchanged_extracted_text(self):
        media = self.RecordingMediaClient([
            {"id": 1, "modified_gmt": "2026-01-01T00:00:00"},
            {"id": 2, "modified_gmt": "2026-02-09T00:00:00"},
        ])
        fetcher = self._fetcher(media)

        records = fetcher.fetch_source(
            "atlas_media_documents",
            previous_records=[
                {
                    "id": 1,
                    "modified_gmt": "2026-01-01T00:00:00",
                    "content_text": "stored text",
                },
                {
                    "id": 2,
                    "modified_gmt": "2026-01-02T00:00:00",
                    "content_text": "stale text",
                },
            ],
            modified_after="2026-02-01T00:00:00+00:00",
            incremental=True,
        )

        # Only the attachment whose modified_gmt moved is downloaded again.
        self.assertEqual(media.read_calls, [[2]])
        by_id = {record["id"]: record["content_text"] for record in records}
        self.assertEqual(by_id[1], "stored text")
        self.assertEqual(by_id[2], "text for 2")
        summary = fetcher.source_summaries["atlas_media_documents"]
        self.assertEqual(summary["reused"], 1)
        self.assertEqual(summary["changed"], 1)

    def test_unreadable_attachments_are_reported_as_failures(self):
        media = self.RecordingMediaClient(
            [{"id": 1, "modified_gmt": "2026-01-01T00:00:00"}],
            failures=[{"attachment_id": 7, "error": "PdfTextUnavailable: no text"}],
        )
        fetcher = self._fetcher(media)

        records = fetcher.fetch_source("atlas_media_documents")

        # A file that cannot be read is surfaced in the manifest, and does
        # not discard the attachments that were read successfully.
        self.assertEqual([record["id"] for record in records], [1])
        summary = fetcher.source_summaries["atlas_media_documents"]
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(summary["failure_details"][0]["attachment_id"], 7)

    def test_missing_media_client_is_a_clear_configuration_error(self):
        fetcher = IngestionFetcher(WordPressClient(), MecClient())

        with self.assertRaises(RuntimeError):
            fetcher.fetch_source("atlas_media_documents")


if __name__ == "__main__":
    unittest.main()
