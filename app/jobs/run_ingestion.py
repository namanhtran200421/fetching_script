import logging
from datetime import datetime, timezone

from app.config import (
    HTTP_MAX_RETRIES,
    HTTP_RETRY_BACKOFF_SECONDS,
    HTTP_TIMEOUT_SECONDS,
    LOCAL_DATA_DIR,
    MAX_CALENDAR_EVENTS,
    MEC_API_TOKEN,
    S3_BUCKET_NAME,
    STORAGE_MODE,
    WORDPRESS_APP_PASSWORD,
    WORDPRESS_BASE_URL,
    WORDPRESS_USERNAME,
    validate_config,
)
from app.clients.http_client import RetryingHttpClient
from app.clients.mec_client import MecCalendarClient
from app.clients.pmpro_client import PMProClient
from app.clients.wordpress_client import WordPressClient
from app.ingestion.fetcher import IngestionFetcher
from app.ingestion.source_registry import WORDPRESS_SOURCES
from app.storage.local_storage import LocalStorage
from app.storage.s3_storage import S3Storage

LOGGER = logging.getLogger(__name__)


def build_storage():
    if STORAGE_MODE == "local":
        return LocalStorage(LOCAL_DATA_DIR)

    if STORAGE_MODE == "s3":
        storage = S3Storage(S3_BUCKET_NAME)
        storage.validate_connection()
        return storage

    raise ValueError("STORAGE_MODE must be 'local' or 's3'")


def run():
    validate_config()
    started_at = datetime.now(timezone.utc)
    run_id = started_at.strftime("%Y%m%d_%H%M%S_%fZ")
    storage = build_storage()
    source_counts = {}
    fetched_sources = {}
    LOGGER.info("Starting ingestion run %s", run_id)

    try:
        with RetryingHttpClient(
            timeout=HTTP_TIMEOUT_SECONDS,
            max_retries=HTTP_MAX_RETRIES,
            backoff_seconds=HTTP_RETRY_BACKOFF_SECONDS,
        ) as http_client:
            wordpress_client = WordPressClient(
                base_url=WORDPRESS_BASE_URL,
                username=WORDPRESS_USERNAME,
                application_password=WORDPRESS_APP_PASSWORD,
                http_client=http_client,
            )
            mec_client = MecCalendarClient(
                base_url=WORDPRESS_BASE_URL,
                username=WORDPRESS_USERNAME,
                application_password=WORDPRESS_APP_PASSWORD,
                api_token=MEC_API_TOKEN,
                http_client=http_client,
            )
            pmpro_client = PMProClient(
                base_url=WORDPRESS_BASE_URL,
                username=WORDPRESS_USERNAME,
                application_password=WORDPRESS_APP_PASSWORD,
                http_client=http_client,
            )
            fetcher = IngestionFetcher(wordpress_client, mec_client)

            # Keep fetched data so MEC definitions are not downloaded twice.
            for source_name in WORDPRESS_SOURCES:
                LOGGER.info("Fetching %s", source_name)
                items = fetcher.fetch_source(source_name)
                fetched_sources[source_name] = items
                source_counts[source_name] = len(items)
                storage.save_source(run_id, source_name, items)
                LOGGER.info("Fetched %d records from %s", len(items), source_name)

            LOGGER.info("Fetching membership plans")
            membership_plans = pmpro_client.get_membership_levels()
            source_counts["membership_plans"] = len(membership_plans)
            storage.save_source(run_id, "membership_plans", membership_plans)

            event_definitions = fetched_sources["mec_event_definitions"]
            calendar_occurrences = fetcher.fetch_calendar_occurrences(
                event_definitions,
                max_events=MAX_CALENDAR_EVENTS,
            )
            storage.save_source(
                run_id,
                "calendar_occurrences",
                calendar_occurrences,
            )

        attempted_events = (
            len(event_definitions)
            if MAX_CALENDAR_EVENTS is None
            else min(MAX_CALENDAR_EVENTS, len(event_definitions))
        )
        failure_count = len(calendar_occurrences["failures"])
        manifest = {
            "run_id": run_id,
            "status": "complete_with_errors" if failure_count else "complete",
            "started_at": started_at.isoformat(),
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "source_counts": source_counts,
            "calendar_resolution": {
                "events_attempted": attempted_events,
                "successful_responses": len(calendar_occurrences["events"]),
                "failures": failure_count,
            },
        }
        manifest_path = storage.save_manifest(run_id, manifest)
        LOGGER.info("Saved manifest to %s", manifest_path)
        LOGGER.info("Ingestion run %s %s", run_id, manifest["status"])
        return manifest
    except Exception as error:
        failure_manifest = {
            "run_id": run_id,
            "status": "failed",
            "started_at": started_at.isoformat(),
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "source_counts": source_counts,
            "error": {
                "type": type(error).__name__,
                "message": str(error),
            },
        }
        try:
            storage.save_manifest(run_id, failure_manifest)
        except Exception:
            LOGGER.exception("Could not save failure manifest for run %s", run_id)
        LOGGER.exception("Ingestion run %s failed", run_id)
        raise


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    run()
