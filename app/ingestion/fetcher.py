import logging

from app.ingestion.source_registry import WORDPRESS_SOURCES


LOGGER = logging.getLogger(__name__)


class IngestionFetcher:

    def __init__(self, wordpress_client, mec_client):
        self.wordpress_client = wordpress_client
        self.mec_client = mec_client

    def fetch_source(self, source_name: str):
        source = WORDPRESS_SOURCES.get(source_name)
        if not source:
            raise ValueError(f"Unknown source: {source_name}")

        return self.wordpress_client.get_all(
            endpoint=source["endpoint"],
            fields=source["fields"]
        )

    def fetch_all_sources(self):
        return {
            name: self.fetch_source(name)
            for name in WORDPRESS_SOURCES
        }

    def fetch_calendar_occurrences(
        self,
        event_definitions: list,
        max_events: int = None
    ):
        results = []
        failures = []
        definitions = (
            event_definitions
            if max_events is None
            else event_definitions[:max_events]
        )

        for index, event in enumerate(definitions, start=1):
            event_id = event.get("id")

            if not event_id:
                failures.append({
                    "event_id": None,
                    "error": "Event definition is missing an id",
                })
                continue

            LOGGER.info(
                "Resolving MEC event %d/%d: %s",
                index,
                len(definitions),
                event_id,
            )

            try:
                response = self.mec_client.get_next_occurrences(event_id)
                results.append({
                    "event_id": event_id,
                    "response": response.json()
                })
            except Exception as error:
                # One broken event should not stop the full run.
                failures.append({
                    "event_id": event_id,
                    "error": str(error)
                })

        return {
            "events": results,
            "failures": failures
        }
