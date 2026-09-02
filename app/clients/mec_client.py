from app.clients.http_client import RetryingHttpClient


class MecCalendarClient:

    def __init__(
        self,
        base_url: str,
        username: str,
        application_password: str,
        api_token: str,
        http_client=None,
    ):
        self.base_url = base_url.rstrip("/")
        self.auth = (username, application_password)
        self.api_token = api_token
        self.http_client = http_client or RetryingHttpClient()

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
