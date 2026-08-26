from datetime import datetime

from app.config import (
    WORDPRESS_BASE_URL,
    WORDPRESS_USERNAME,
    WORDPRESS_APP_PASSWORD
)

from app.clients.wordpress_client import WordPressClient
from app.ingestion.fetcher import IngestionFetcher
from app.ingestion.source_registry import WORDPRESS_SOURCES
from app.storage.local_storage import LocalStorage


def run():

    run_id = datetime.now().strftime(
        "%Y%m%d_%H%M%S"
    )

    print(
        f"Starting ingestion run: {run_id}"
    )

    wordpress_client = WordPressClient(
        base_url=WORDPRESS_BASE_URL,
        username=WORDPRESS_USERNAME,
        application_password=WORDPRESS_APP_PASSWORD
    )

    fetcher = IngestionFetcher(
        wordpress_client=wordpress_client
    )

    storage = LocalStorage()

    source_counts = {}

    for source_name in WORDPRESS_SOURCES:

        print(
            f"Fetching {source_name}..."
        )

        items = fetcher.fetch_source(
            source_name
        )

        source_counts[source_name] = len(items)

        file_path = storage.save_source(
            run_id=run_id,
            source_name=source_name,
            items=items
        )

        print(
            f"Saved {len(items)} records "
            f"to {file_path}"
        )

    manifest = {
        "run_id": run_id,
        "source_counts": source_counts
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