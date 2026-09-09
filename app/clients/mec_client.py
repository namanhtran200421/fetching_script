import asyncio
import logging

from app.clients.http_client import AsyncRetryingHttpClient, RetryingHttpClient


LOGGER = logging.getLogger(__name__)


class MecCalendarClient:

    def __init__(
        self,
        base_url: str,
        username: str,
        application_password: str,
        api_token: str,
        http_client=None,
        async_concurrency=6,
        timeout=50,
        max_retries=3,
        backoff_seconds=1,
        max_response_bytes=5 * 1024 * 1024,
    ):
        self.base_url = base_url.rstrip("/")
        self.auth = (username, application_password)
        self.api_token = api_token
        self.http_client = http_client or RetryingHttpClient()
        self.async_concurrency = async_concurrency
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds
        self.max_response_bytes = max_response_bytes

    def get_next_occurrences(self, event_id: int):
        url = (
            f"{self.base_url}/wp-json/mec/v1.0/events/"
            f"{event_id}/next-occurrences"
        )

        response = self.http_client.get(
            url,
            auth=self.auth,
            headers={"mec-token": self.api_token},
        )
        response.raise_for_status()

        return response

    async def _get_event_details_async(self, search_results):
        semaphore = asyncio.Semaphore(self.async_concurrency)
        headers = {"mec-token": self.api_token}
        async with AsyncRetryingHttpClient(
            timeout=self.timeout,
            max_retries=self.max_retries,
            backoff_seconds=self.backoff_seconds,
            max_connections=self.async_concurrency,
        ) as http_client:
            async def fetch_one(search_result):
                event_id = search_result.get("id")
                if not event_id:
                    return None, {
                        "event_id": None,
                        "error": "MEC search result is missing an id",
                    }
                url = (
                    f"{self.base_url}/wp-json/mec/v1.0/events/{event_id}"
                )
                try:
                    async with semaphore:
                        response = await http_client.get(
                            url,
                            auth=self.auth,
                            headers=headers,
                        )
                    if len(response.content) > self.max_response_bytes:
                        raise ValueError(
                            "MEC event response exceeds the configured size limit"
                        )
                    payload = response.json()
                    if not isinstance(payload, dict):
                        raise ValueError("MEC event response is not an object")
                    return {
                        "search_result": search_result,
                        "response": payload,
                    }, None
                except Exception as error:
                    return None, {
                        "event_id": event_id,
                        "error": f"{type(error).__name__}: {error}",
                    }

            outcomes = []
            batch_size = max(20, self.async_concurrency * 10)
            for offset in range(0, len(search_results), batch_size):
                batch = search_results[offset:offset + batch_size]
                outcomes.extend(await asyncio.gather(*(
                    fetch_one(item) for item in batch
                )))
                LOGGER.info(
                    "Fetched MEC event details %d/%d",
                    min(offset + len(batch), len(search_results)),
                    len(search_results),
                )
        events = [event for event, _ in outcomes if event is not None]
        failures = [failure for _, failure in outcomes if failure is not None]
        return {"events": events, "failures": failures}

    def get_event_details(self, search_results):
        return asyncio.run(self._get_event_details_async(search_results))
