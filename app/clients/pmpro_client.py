from app.clients.http_client import RetryingHttpClient


class PMProClient:

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

    def get_membership_levels(self):
        response = self.http_client.get(
            f"{self.base_url}/wp-json/pmpro/v1/membership_levels",
            auth=self.auth,
        )
        response.raise_for_status()

        return response.json()
