import asyncio
import unittest

from app.clients.wordpress_client import WordPressClient


class FakeResponse:
    def __init__(self, payload, total_pages=1, total=None):
        self._payload = payload
        self.headers = {"X-WP-TotalPages": str(total_pages)}
        if total is not None:
            self.headers["X-WP-Total"] = str(total)

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


class SyncFirstPageClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


class AsyncPageClient:
    def __init__(self, payloads):
        self.payloads = payloads
        self.calls = []
        self.active = 0
        self.max_active = 0

    async def get(self, url, **kwargs):
        page = kwargs["params"]["page"]
        self.calls.append((url, kwargs))
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0.01)
            return FakeResponse(self.payloads[page])
        finally:
            self.active -= 1


class AsyncClientContext:
    def __init__(self, client):
        self.client = client

    async def __aenter__(self):
        return self.client

    async def __aexit__(self, exc_type, exc_value, traceback):
        return None


class WordPressClientTests(unittest.TestCase):
    def test_collection_remaining_pages_are_fetched_concurrently_in_order(self):
        sync_client = SyncFirstPageClient(
            FakeResponse([{"id": 1}], total_pages=4)
        )
        async_client = AsyncPageClient({
            2: [{"id": 2}],
            3: [{"id": 3}],
            4: [{"id": 4}],
        })
        client = WordPressClient(
            "https://example.test",
            "user",
            "password",
            http_client=sync_client,
            async_concurrency=2,
            async_http_client_factory=(
                lambda **kwargs: AsyncClientContext(async_client)
            ),
        )

        records = client.get_all("pages", "id")

        self.assertEqual([record["id"] for record in records], [1, 2, 3, 4])
        self.assertEqual(async_client.max_active, 2)
        self.assertEqual(len(async_client.calls), 3)

    def test_modified_after_is_sent_with_modified_ordering(self):
        sync_client = SyncFirstPageClient(FakeResponse([], total_pages=1))
        client = WordPressClient(
            "https://example.test",
            "user",
            "password",
            http_client=sync_client,
        )

        client.get_all(
            "pages",
            "id,modified_gmt",
            modified_after="2026-09-01T00:00:00Z",
        )

        params = sync_client.calls[0][1]["params"]
        self.assertEqual(params["modified_after"], "2026-09-01T00:00:00Z")
        self.assertEqual(params["orderby"], "modified")
        self.assertEqual(params["order"], "asc")


class UnauthenticatedClientTests(unittest.TestCase):
    """A public origin must be fetched without any credential at all."""

    def test_client_without_credentials_sends_no_auth(self):
        sync_client = SyncFirstPageClient(FakeResponse([{"id": 1}]))
        client = WordPressClient(
            "https://atlas.example.test",
            http_client=sync_client,
        )

        client.get_all("posts", "id")

        self.assertIsNone(client.auth)
        self.assertIsNone(sync_client.calls[0][1]["auth"])

    def test_half_a_credential_pair_never_authenticates(self):
        # A misconfigured environment must not send an empty password
        # alongside a real username.
        for username, password in (("user", None), (None, "password"), ("", "")):
            with self.subTest(username=username, password=password):
                client = WordPressClient(
                    "https://atlas.example.test", username, password
                )
                self.assertIsNone(client.auth)

    def test_extra_params_reach_every_page_of_a_collection(self):
        sync_client = SyncFirstPageClient(
            FakeResponse([{"id": 1}], total_pages=2)
        )
        async_client = AsyncPageClient({2: [{"id": 2}]})
        client = WordPressClient(
            "https://atlas.example.test",
            http_client=sync_client,
            async_concurrency=2,
            async_http_client_factory=(
                lambda **kwargs: AsyncClientContext(async_client)
            ),
        )

        client.get_all(
            "media", "id", extra_params={"mime_type": "application/pdf"}
        )

        self.assertEqual(
            sync_client.calls[0][1]["params"]["mime_type"], "application/pdf"
        )
        self.assertEqual(
            async_client.calls[0][1]["params"]["mime_type"], "application/pdf"
        )

    def test_get_terms_paginates_without_a_post_status(self):
        sync_client = SyncFirstPageClient(
            FakeResponse([{"id": 1, "name": "Insights"}], total_pages=2)
        )
        async_client = AsyncPageClient({2: [{"id": 2, "name": "News"}]})
        client = WordPressClient(
            "https://atlas.example.test",
            http_client=sync_client,
            async_concurrency=2,
            async_http_client_factory=(
                lambda **kwargs: AsyncClientContext(async_client)
            ),
        )

        terms = client.get_terms("categories", fields="id,name")

        self.assertEqual([term["id"] for term in terms], [1, 2])
        params = sync_client.calls[0][1]["params"]
        self.assertNotIn("status", params)
        self.assertEqual(params["hide_empty"], "false")
        self.assertEqual(params["_fields"], "id,name")


if __name__ == "__main__":
    unittest.main()
