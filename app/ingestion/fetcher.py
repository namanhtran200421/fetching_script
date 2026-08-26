from app.ingestion.source_registry import WORDPRESS_SOURCES

class IngestionFetcher:
    def __init__(self, wordpress_client):
        self.wordpress_client = wordpress_client


    def fetch_source(self, source_name:str):

        if source_name not in WORDPRESS_SOURCES:
            raise ValueError( f'Unknown source: {source_name}')
        
        source_config = WORDPRESS_SOURCES[source_name]

        endpoint = source_config['endpoint']
        fields =source_config['fields']

        items = self.wordpress_client.get_all(endpoint=endpoint, fields=fields)

        return items


    def fetch_all_sources(self):
        results = {}

        for source_name in WORDPRESS_SOURCES:
            items = self.fetch_source(source_name)
            results[source_name] = items

        return results

