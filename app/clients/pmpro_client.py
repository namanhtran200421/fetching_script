import httpx

class PmproClient:
    def __init__(self, base_url:str, username:str, application_password:str):
        self.base_url = base_url
        self.username =username
        self.application_password = application_password


    def get_membership_levels(self):
        url = (
            f"{self.base_url}"
            "/wp-json/pmpro/v1/membership_levels"
        )

        response = httpx.get(url, auth=(self.username, self.application_password), 
                    timeout=50
        )

        response.raise_for_status()

        return response.json()

