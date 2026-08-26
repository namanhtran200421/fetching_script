from datetime import datetime

from app.config import (
    MEC_API_TOKEN,
    WORDPRESS_BASE_URL,
    WORDPRESS_USERNAME,
    WORDPRESS_APP_PASSWORD,
    STORAGE_MODE,
    S3_BUCKET_NAME
)

from app.clients.wordpress_client import WordPressClient
from app.clients.mec_client import MecCalendarClient
from app.ingestion.fetcher import IngestionFetcher
from app.ingestion.source_registry import WORDPRESS_SOURCES
from app.storage.local_storage import LocalStorage
from app.storage.s3_storage import S3Storage


def run():

    run_id = datetime.now().strftime(
        "%Y%m%d_%H%M%S"
    )

    print(
        f"Starting ingestion run: {run_id}"
    )

    # ---------------------------------
    # Clients
    # ---------------------------------

    wordpress_client = WordPressClient(
        base_url=WORDPRESS_BASE_URL,
        username=WORDPRESS_USERNAME,
        application_password=WORDPRESS_APP_PASSWORD
    )

    mec_client = MecCalendarClient(
        base_url=WORDPRESS_BASE_URL,
        username=WORDPRESS_USERNAME,
        application_password=WORDPRESS_APP_PASSWORD,
        api_token=MEC_API_TOKEN
    )

    # ---------------------------------
    # Fetcher
    # ---------------------------------

    fetcher = IngestionFetcher(
        wordpress_client=wordpress_client,
        mec_client=mec_client
    )

    if STORAGE_MODE == "s3":

        if not S3_BUCKET_NAME:
            raise ValueError(
                "S3_BUCKET_NAME is required "
                "when STORAGE_MODE=s3"
            )

        storage = S3Storage(
            bucket_name=S3_BUCKET_NAME
        )

    else:
        storage = LocalStorage()

    source_counts = {}

    # Keep fetched data so we do not
    # download MEC definitions twice
    fetched_sources = {}

    # ---------------------------------
    # Fetch normal WordPress sources
    # ---------------------------------

    for source_name in WORDPRESS_SOURCES:

        print(
            f"Fetching {source_name}..."
        )

        items = fetcher.fetch_source(
            source_name
        )

        fetched_sources[source_name] = items

        source_counts[source_name] = len(items)

        storage.save_source(
            run_id=run_id,
            source_name=source_name,
            data=items
        )

        print(
            f"Fetched {len(items)} "
            f"records from {source_name}"
        )

    # ---------------------------------
    # Resolve MEC occurrences
    # ---------------------------------

    event_definitions = fetched_sources[
        "mec_event_definitions"
    ]

    calendar_occurrences = (
        fetcher.fetch_calendar_occurrences(
            event_definitions=event_definitions,
            max_events=5
        )
    )

    storage.save_source(
        run_id=run_id,
        source_name="calendar_occurrences",
        data=calendar_occurrences
    )

    # ---------------------------------
    # Manifest
    # ---------------------------------

    manifest = {
        "run_id": run_id,
        "source_counts": source_counts,
        "calendar_resolution": {
            "events_attempted": 5,
            "successful_responses": len(
                calendar_occurrences["events"]
            ),
            "failures": len(
                calendar_occurrences["failures"]
            )
        }
    }

    manifest_path = storage.save_manifest(
        run_id=run_id,
        manifest=manifest
    )

    print(
        f"Saved manifest to {manifest_path}"
    )

    print(
        f"Ingestion run {run_id} complete."
    )


if __name__ == "__main__":
    run()
