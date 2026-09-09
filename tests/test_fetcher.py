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


if __name__ == "__main__":
    unittest.main()
