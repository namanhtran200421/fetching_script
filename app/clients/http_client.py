import logging
import time

import httpx


LOGGER = logging.getLogger(__name__)
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class RetryingHttpClient:
    """Small httpx wrapper with connection pooling and bounded retries."""

    def __init__(
        self,
        timeout: float = 50,
        max_retries: int = 3,
        backoff_seconds: float = 1,
        client=None,
        sleep=time.sleep,
    ):
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds
        self.client = client or httpx.Client()
        self._owns_client = client is None
        self.sleep = sleep

    def get(self, url: str, **kwargs):
        kwargs.setdefault("timeout", self.timeout)

        for attempt in range(self.max_retries + 1):
            try:
                response = self.client.get(url, **kwargs)
            except httpx.TransportError:
                if attempt == self.max_retries:
                    raise
                self._wait(attempt, url)
                continue

            if (
                response.status_code not in RETRYABLE_STATUS_CODES
                or attempt == self.max_retries
            ):
                response.raise_for_status()
                return response

            self._wait(attempt, url, response)

        raise RuntimeError("HTTP retry loop ended unexpectedly")

    def _wait(self, attempt, url, response=None):
        delay = self.backoff_seconds * (2 ** attempt)
        retry_after = response.headers.get("Retry-After") if response else None
        if retry_after:
            try:
                delay = max(delay, float(retry_after))
            except ValueError:
                pass

        LOGGER.warning(
            "Transient HTTP failure for %s; retrying in %.1f seconds",
            url,
            delay,
        )
        self.sleep(delay)

    def close(self):
        if self._owns_client:
            self.client.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
