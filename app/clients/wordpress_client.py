from app.clients.http_client import RetryingHttpClient


class WordPressClient:

    def __init__(
        self,
        base_url: str,
        username: str,
        application_password: str,
        http_client=None,
    ):
        self.base_url = base_url.rstrip("/")
        self.auth = (username, application_password)
        self.http_client = http_client or RetryingHttpClient()

    def get_page(
        self,
        endpoint: str,
        page: int,
        fields: str,
        per_page: int = 100
    ):
        params = {
            "status": "publish",
            "page": page,
            "per_page": per_page
        }

        if fields:
            params["_fields"] = fields

        response = self.http_client.get(
            f"{self.base_url}/wp-json/wp/v2/{endpoint}",
            auth=self.auth,
            params=params,
        )
        response.raise_for_status()

        return response

    def get_all(self, endpoint: str, fields: str):
        first_response = self.get_page(endpoint, 1, fields)
        all_items = first_response.json()
        total_pages = int(first_response.headers["X-WP-TotalPages"])

        for page in range(2, total_pages + 1):
            response = self.get_page(endpoint, page, fields)
            all_items.extend(response.json())

        return all_items
