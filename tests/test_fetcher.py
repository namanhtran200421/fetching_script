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


class FetcherTests(unittest.TestCase):
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
