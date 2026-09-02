import unittest

import httpx

from app.clients.http_client import RetryingHttpClient


class FakeResponse:
    def __init__(self, status_code=200, headers=None):
        self.status_code = status_code
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("GET", "https://example.test")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError(
                "test response",
                request=request,
                response=response,
            )


class FakeClient:
    def __init__(self, outcomes):
        self.outcomes = iter(outcomes)
        self.call_count = 0

    def get(self, url, **kwargs):
        self.call_count += 1
        outcome = next(self.outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class RetryingHttpClientTests(unittest.TestCase):
    def test_retries_retryable_status(self):
        fake = FakeClient([FakeResponse(503), FakeResponse(200)])
        delays = []
        client = RetryingHttpClient(
            client=fake,
            max_retries=2,
            backoff_seconds=0.25,
            sleep=delays.append,
        )

        response = client.get("https://example.test")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(fake.call_count, 2)
        self.assertEqual(delays, [0.25])

    def test_retries_transport_error(self):
        request = httpx.Request("GET", "https://example.test")
        fake = FakeClient([
            httpx.ConnectError("temporary", request=request),
            FakeResponse(200),
        ])
        client = RetryingHttpClient(
            client=fake,
            max_retries=1,
            backoff_seconds=0,
            sleep=lambda delay: None,
        )

        self.assertEqual(client.get("https://example.test").status_code, 200)
        self.assertEqual(fake.call_count, 2)

    def test_does_not_retry_non_retryable_status(self):
        fake = FakeClient([FakeResponse(404)])
        client = RetryingHttpClient(client=fake, max_retries=3)

        with self.assertRaises(httpx.HTTPStatusError):
            client.get("https://example.test")

        self.assertEqual(fake.call_count, 1)


if __name__ == "__main__":
    unittest.main()
