from app.ingestion.source_registry import WORDPRESS_SOURCES

class IngestionFetcher:
    def __init__(self, wordpress_client, mec_client):
        self.wordpress_client = wordpress_client
        self.mec_client = mec_client


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


    def fetch_calendar_occurrences(
        self,
        event_definitions: list,
        max_events: int = None
    ):
        results = []
        failures = []

        definitions = event_definitions

        if max_events is not None:
            definitions = definitions[:max_events]

        for index, event in enumerate(
            definitions,
            start=1
        ):
            event_id = event.get("id")

            if not event_id:
                continue

            print(
                f"Resolving MEC event "
                f"{index}/{len(definitions)}: "
                f"{event_id}"
            )

            try:
                response = self.mec_client.get_next_occurrences(
                    event_id=event_id
                )

                data = response.json()

                results.append({
                    "event_id": event_id,
                    "response": data
                })

            except Exception as error:
                failures.append({
                    "event_id": event_id,
                    "error": str(error)
                })

        return {
            "events": results,
            "failures": failures
        }