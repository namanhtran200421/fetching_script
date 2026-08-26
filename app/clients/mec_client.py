import httpx


class MecCalendarClient:

    def __init__(
        self,
        base_url: str,
        username: str,
        application_password: str,
        api_token: str
    ):
        self.base_url = base_url
        self.username = username
        self.application_password = application_password
        self.api_token = api_token

    def get_next_occurrences(
        self,
        event_id: int
    ):
        url = (
            f"{self.base_url}"
            f"/wp-json/mec/v1.0/events/"
            f"{event_id}/next-occurrences"
        )

        response = httpx.get(
            url,
            auth=(
                self.username,
                self.application_password
            ),
            headers={
                "mec-token": self.api_token
            },
            timeout=30
        )

        response.raise_for_status()

        return response