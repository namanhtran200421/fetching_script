import asyncio

from app.clients.http_client import AsyncRetryingHttpClient, RetryingHttpClient


class WordPressClient:

    def __init__(
        self,
        base_url: str,
        username: str,
        application_password: str,
        http_client=None,
        async_concurrency=6,
        timeout=50,
        max_retries=3,
        backoff_seconds=1,
        async_http_client_factory=AsyncRetryingHttpClient,
    ):
        self.base_url = base_url.rstrip("/")
        self.auth = (username, application_password)
        self.http_client = http_client or RetryingHttpClient()
        self.async_concurrency = async_concurrency
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds
        self.async_http_client_factory = async_http_client_factory

    @staticmethod
    def _collection_params(
        page,
        per_page,
        fields=None,
        include=None,
        modified_after=None,
    ):
        params = {
            "status": "publish",
            "page": page,
            "per_page": per_page,
        }
        if fields:
            params["_fields"] = fields
        if include:
            params["include"] = ",".join(str(item) for item in include)
        if modified_after:
            params.update({
                "modified_after": modified_after,
                "orderby": "modified",
                "order": "asc",
            })
        return params

    def get_page(
        self,
        endpoint: str,
        page: int,
        fields: str,
        per_page: int = 100,
        include=None,
        modified_after=None,
    ):
        params = self._collection_params(
            page,
            per_page,
            fields=fields,
            include=include,
            modified_after=modified_after,
        )

        response = self.http_client.get(
            f"{self.base_url}/wp-json/wp/v2/{endpoint}",
            auth=self.auth,
            params=params,
        )
        response.raise_for_status()

        return response

    async def _get_pages_async(self, endpoint, page_params):
        semaphore = asyncio.Semaphore(self.async_concurrency)
        url = f"{self.base_url}/wp-json/wp/v2/{endpoint}"
        async with self.async_http_client_factory(
            timeout=self.timeout,
            max_retries=self.max_retries,
            backoff_seconds=self.backoff_seconds,
            max_connections=self.async_concurrency,
        ) as http_client:
            async def fetch_one(params):
                async with semaphore:
                    response = await http_client.get(
                        url,
                        auth=self.auth,
                        params=params,
                    )
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, list):
                    raise ValueError(
                        "WordPress collection response must be a list"
                    )
                return payload

            return await asyncio.gather(*(
                fetch_one(params) for params in page_params
            ))

    def _remaining_pages(self, endpoint, page_params):
        if not page_params:
            return []
        return asyncio.run(self._get_pages_async(endpoint, page_params))

    def get_all(
        self,
        endpoint: str,
        fields: str,
        include=None,
        modified_after=None,
    ):
        if include is not None and not include:
            return []

        first_response = self.get_page(
            endpoint,
            1,
            fields,
            include=include,
            modified_after=modified_after,
        )
        all_items = first_response.json()
        if not isinstance(all_items, list):
            raise ValueError("WordPress collection response must be a list")
        total_pages = int(first_response.headers.get("X-WP-TotalPages", "1"))

        page_params = [
            self._collection_params(
                page,
                100,
                fields=fields,
                include=include,
                modified_after=modified_after,
            )
            for page in range(2, total_pages + 1)
        ]
        for payload in self._remaining_pages(endpoint, page_params):
            all_items.extend(payload)

        return all_items

    def search_all(
        self, subtype: str, per_page: int = 100, max_results: int = 5000
    ):
        """Return the complete searchable inventory for a post subtype."""
        endpoint = f"{self.base_url}/wp-json/wp/v2/search"
        params = {
            "type": "post",
            "subtype": subtype,
            "per_page": per_page,
            "page": 1,
            "_fields": "id,title,url,subtype",
        }
        first_response = self.http_client.get(
            endpoint,
            auth=self.auth,
            params=params,
        )
        first_response.raise_for_status()
        results = first_response.json()
        if not isinstance(results, list):
            raise ValueError("WordPress search response must be a list")
        total_pages = int(first_response.headers.get("X-WP-TotalPages", "1"))
        total_results = int(
            first_response.headers.get("X-WP-Total", str(len(results)))
        )
        if total_results > max_results:
            raise ValueError(
                "WordPress search inventory exceeds the configured "
                f"limit of {max_results}"
            )

        page_params = [
            {**params, "page": page}
            for page in range(2, total_pages + 1)
        ]
        for payload in self._remaining_pages("search", page_params):
            results.extend(payload)

        unique = []
        seen = set()
        for item in results:
            if not isinstance(item, dict):
                continue
            identity = item.get("id") or item.get("url")
            if identity in seen:
                continue
            seen.add(identity)
            unique.append(item)
        return unique
