import httpx

'''
Wordpress client: Communication with Wordpress
'''
class WordPressClient:

    def __init__(
        self,
        base_url: str,
        username: str,
        application_password: str
    ):
        self.base_url = base_url
        self.username = username
        self.application_password = application_password

    def get_page(self, page: int, endpoint: str, per_page: int = 100, fields: str = 'id, title, modified, link'):

        url = f'{self.base_url}/wp-json/wp/v2/{endpoint}'

        params = {
                'status': 'publish',
                'page': page,
                'per_page':per_page, 
        }

        if fields:
            params['_fields'] = fields

        response = httpx.get(url, auth=(
            self.username, self.application_password
        ), params=params, timeout=30
        
        )
        response.raise_for_status()

        return response

    def get_all(self, endpoint:str, fields="id, title, modified, link"):

        first_response = self.get_page(endpoint=endpoint, page=1, fields=fields)
        all_items = first_response.json()

        total_pages = int(first_response.headers['X-WP-TotalPages']
        )

        for page in range(2, total_pages + 1):

            response = self.get_page(endpoint=endpoint, page=page, fields=fields)
            items = response.json()

            all_items.extend(items)

        return all_items

        