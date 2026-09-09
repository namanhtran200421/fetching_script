import logging
from datetime import datetime, timezone

from app.config import (
    FULL_RECONCILIATION_INTERVAL_DAYS,
    HTTP_MAX_RETRIES,
    HTTP_RETRY_BACKOFF_SECONDS,
    HTTP_TIMEOUT_SECONDS,
    HTML_CRAWL_CONCURRENCY,
    HTML_CRAWL_BURST_SIZE,
    HTML_CRAWL_MIN_REQUEST_INTERVAL_SECONDS,
    HTML_CRAWL_MAX_PAGES,
    HTML_CRAWL_MAX_SITEMAPS,
    HTML_CRAWL_MAX_REDIRECTS,
    HTML_CRAWL_MAX_RESPONSE_BYTES,
    HTML_CRAWL_RESPECT_ROBOTS,
    INCREMENTAL_INGESTION,
    INCREMENTAL_LOOKBACK_SECONDS,
    LOCAL_DATA_DIR,
    MAX_CALENDAR_EVENTS,
    MEC_API_TOKEN,
    CLEANED_S3_BUCKET_NAME,
    RAW_S3_BUCKET_NAME,
    STORAGE_MODE,
    WORDPRESS_APP_PASSWORD,
    WORDPRESS_BASE_URL,
    WORDPRESS_PAGE_ALLOWLIST,
    WORDPRESS_USERNAME,
    validate_config,
)
from app.clients.http_client import RetryingHttpClient
from app.clients.mec_client import MecCalendarClient
from app.clients.pmpro_client import PMProClient
from app.clients.wordpress_client import WordPressClient
from app.ingestion.fetcher import IngestionFetcher
from app.ingestion.html_crawler import HtmlCrawler
from app.ingestion.incremental import build_incremental_plan
from app.ingestion.source_registry import SOURCE_MINIMUM_COUNTS, SOURCES
from app.storage.local_storage import LocalStorage
from app.storage.s3_storage import S3Storage

LOGGER = logging.getLogger(__name__)


def _load_previous_manifest(storage):
    load_pointer = getattr(storage, "load_raw_latest", None)
    if load_pointer is None:
        return None
    try:
        pointer = load_pointer()
        if not pointer or not pointer.get("run_id"):
            return None
        manifest = storage.load_manifest(pointer["run_id"])
        eligible = manifest.get(
            "baseline_eligible",
            manifest.get("status") == "complete",
        )
        return manifest if eligible else None
    except Exception as error:
        LOGGER.warning(
            "Could not load the previous raw baseline; running a full fetch: %s",
            error,
        )
        return None


def build_storage():
    if STORAGE_MODE == "local":
        return LocalStorage(LOCAL_DATA_DIR)

    if STORAGE_MODE == "s3":
        storage = S3Storage(
            RAW_S3_BUCKET_NAME,
            cleaned_bucket_name=CLEANED_S3_BUCKET_NAME,
        )
        storage.validate_connection()
        return storage

    raise ValueError("STORAGE_MODE must be 'local' or 's3'")


def run(storage=None, started_at=None, run_id=None):
    validate_config()
    started_at = started_at or datetime.now(timezone.utc)
    run_id = run_id or started_at.strftime("%Y-%m-%d_%H-%M-%S_%f_UTC")
    storage = storage or build_storage()
    source_counts = {}
    fetched_sources = {}
    source_failures = []
    previous_manifest = _load_previous_manifest(storage)
    incremental_plan = build_incremental_plan(
        started_at,
        previous_manifest,
        enabled=INCREMENTAL_INGESTION,
        reconciliation_interval_days=FULL_RECONCILIATION_INTERVAL_DAYS,
        lookback_seconds=INCREMENTAL_LOOKBACK_SECONDS,
    )
    incremental_run = incremental_plan["mode"] == "incremental"
    previous_run_id = incremental_plan.get("previous_run_id")
    LOGGER.info("Starting ingestion run %s", run_id)
    LOGGER.info(
        "Ingestion mode is %s: %s",
        incremental_plan["mode"],
        incremental_plan["reason"],
    )

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
                async_concurrency=HTML_CRAWL_CONCURRENCY,
                timeout=HTTP_TIMEOUT_SECONDS,
                max_retries=HTTP_MAX_RETRIES,
                backoff_seconds=HTTP_RETRY_BACKOFF_SECONDS,
            )
            mec_client = MecCalendarClient(
                base_url=WORDPRESS_BASE_URL,
                username=WORDPRESS_USERNAME,
                application_password=WORDPRESS_APP_PASSWORD,
                api_token=MEC_API_TOKEN,
                http_client=http_client,
                async_concurrency=HTML_CRAWL_CONCURRENCY,
                timeout=HTTP_TIMEOUT_SECONDS,
                max_retries=HTTP_MAX_RETRIES,
                backoff_seconds=HTTP_RETRY_BACKOFF_SECONDS,
                max_response_bytes=HTML_CRAWL_MAX_RESPONSE_BYTES,
            )
            pmpro_client = PMProClient(
                base_url=WORDPRESS_BASE_URL,
                username=WORDPRESS_USERNAME,
                application_password=WORDPRESS_APP_PASSWORD,
                http_client=http_client,
            )
            html_crawler = HtmlCrawler(
                base_url=WORDPRESS_BASE_URL,
                auth=(WORDPRESS_USERNAME, WORDPRESS_APP_PASSWORD),
                max_pages=HTML_CRAWL_MAX_PAGES,
                max_sitemaps=HTML_CRAWL_MAX_SITEMAPS,
                max_redirects=HTML_CRAWL_MAX_REDIRECTS,
                concurrency=HTML_CRAWL_CONCURRENCY,
                burst_size=HTML_CRAWL_BURST_SIZE,
                minimum_request_interval=(
                    HTML_CRAWL_MIN_REQUEST_INTERVAL_SECONDS
                ),
                respect_robots=HTML_CRAWL_RESPECT_ROBOTS,
                max_response_bytes=HTML_CRAWL_MAX_RESPONSE_BYTES,
                timeout=HTTP_TIMEOUT_SECONDS,
                max_retries=HTTP_MAX_RETRIES,
                backoff_seconds=HTTP_RETRY_BACKOFF_SECONDS,
            )
            external_html_crawlers = {}
            external_crawlers_by_origin = {}
            for source_name, source_config in SOURCES.items():
                source_base_url = source_config.get("base_url")
                if not source_base_url:
                    continue
                # A separate crawler is a credential boundary: application
                # passwords must never be sent to linked external hosts. Reuse
                # one unauthenticated crawler per origin so future same-host
                # sources share one robots policy and one request-rate budget.
                origin = source_base_url.rstrip("/").lower()
                crawler = external_crawlers_by_origin.get(origin)
                if crawler is None:
                    crawler = HtmlCrawler(
                        base_url=source_base_url,
                        auth=None,
                        max_pages=HTML_CRAWL_MAX_PAGES,
                        max_sitemaps=HTML_CRAWL_MAX_SITEMAPS,
                        max_redirects=HTML_CRAWL_MAX_REDIRECTS,
                        concurrency=HTML_CRAWL_CONCURRENCY,
                        burst_size=HTML_CRAWL_BURST_SIZE,
                        minimum_request_interval=(
                            HTML_CRAWL_MIN_REQUEST_INTERVAL_SECONDS
                        ),
                        respect_robots=HTML_CRAWL_RESPECT_ROBOTS,
                        max_response_bytes=HTML_CRAWL_MAX_RESPONSE_BYTES,
                        timeout=HTTP_TIMEOUT_SECONDS,
                        max_retries=HTTP_MAX_RETRIES,
                        backoff_seconds=HTTP_RETRY_BACKOFF_SECONDS,
                    )
                    external_crawlers_by_origin[origin] = crawler
                external_html_crawlers[source_name] = crawler
            fetcher = IngestionFetcher(
                wordpress_client,
                mec_client,
                html_crawler=html_crawler,
                html_crawlers=external_html_crawlers,
                wordpress_page_allowlist=WORDPRESS_PAGE_ALLOWLIST,
                max_calendar_events=MAX_CALENDAR_EVENTS,
            )

            # Keep fetched data so MEC definitions are not downloaded twice.
            for source_name in SOURCES:
                LOGGER.info("Fetching %s", source_name)
                previous_records = None
                if incremental_run and previous_run_id:
                    try:
                        previous_records = storage.load_source(
                            previous_run_id,
                            source_name,
                        )
                    except Exception as error:
                        LOGGER.warning(
                            "Could not reuse %s from baseline %s; fetching it "
                            "in full: %s",
                            source_name,
                            previous_run_id,
                            error,
                        )
                try:
                    items = fetcher.fetch_source(
                        source_name,
                        previous_records=previous_records,
                        modified_after=incremental_plan.get("modified_after"),
                        incremental=incremental_run,
                    )
                except Exception as error:
                    items = []
                    source_failures.append({
                        "source": source_name,
                        "error": {
                            "type": type(error).__name__,
                            "message": str(error),
                        },
                    })
                    LOGGER.exception(
                        "Source %s failed; continuing the run", source_name
                    )
                fetched_sources[source_name] = items
                source_counts[source_name] = len(items)
                storage.save_source(run_id, source_name, items)
                LOGGER.info("Fetched %d records from %s", len(items), source_name)

            LOGGER.info("Fetching membership plans")
            try:
                membership_plans = pmpro_client.get_membership_levels()
            except Exception as error:
                membership_plans = []
                source_failures.append({
                    "source": "membership_plans",
                    "error": {
                        "type": type(error).__name__,
                        "message": str(error),
                    },
                })
                LOGGER.exception(
                    "Membership plans failed; continuing the run"
                )
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
            source_counts["calendar_occurrences"] = len(
                calendar_occurrences["events"]
            )

        attempted_events = (
            len(event_definitions)
            if MAX_CALENDAR_EVENTS is None
            else min(MAX_CALENDAR_EVENTS, len(event_definitions))
        )
        failure_count = len(calendar_occurrences["failures"])
        crawl_failure_count = sum(
            summary["failed"] for summary in fetcher.source_summaries.values()
        )
        crawl_has_empty_or_truncated_source = any(
            summary["accepted"] == 0 or summary.get("limit_reached", False)
            for summary in fetcher.source_summaries.values()
        )
        api_fallback_count = sum(
            bool(summary.get("api_fallback"))
            for summary in fetcher.source_summaries.values()
        )
        quality_gate_violations = [
            {
                "source": source_name,
                "minimum": minimum,
                "actual": source_counts.get(source_name, 0),
            }
            for source_name, minimum in SOURCE_MINIMUM_COUNTS.items()
            if source_counts.get(source_name, 0) < minimum
        ]
        baseline_eligible = not quality_gate_violations and not source_failures
        completed_at = datetime.now(timezone.utc)
        manifest = {
            "run_id": run_id,
            "status": (
                "complete_with_errors"
                if (
                    failure_count
                    or source_failures
                    or crawl_failure_count
                    or crawl_has_empty_or_truncated_source
                    or api_fallback_count
                    or quality_gate_violations
                )
                else "complete"
            ),
            "started_at": started_at.isoformat(),
            "completed_at": completed_at.isoformat(),
            "duration_seconds": round(
                (completed_at - started_at).total_seconds(), 3
            ),
            "incremental": incremental_plan,
            "last_full_reconciliation_at": incremental_plan[
                "last_full_reconciliation_at"
            ],
            "baseline_eligible": baseline_eligible,
            "source_counts": source_counts,
            "source_failures": source_failures,
            "html_crawls": {
                name: summary
                for name, summary in fetcher.source_summaries.items()
                if summary.get("acquisition_method") == "html"
            },
            "source_fetches": fetcher.source_summaries,
            "api_fallback_count": api_fallback_count,
            "quality_gate": {
                "passed": not quality_gate_violations,
                "minimum_source_counts": SOURCE_MINIMUM_COUNTS,
                "violations": quality_gate_violations,
            },
            "calendar_resolution": {
                "events_attempted": attempted_events,
                "successful_responses": len(calendar_occurrences["events"]),
                "failures": failure_count,
            },
        }
        manifest_path = storage.save_manifest(run_id, manifest)
        if baseline_eligible:
            publish_latest = getattr(storage, "publish_raw_latest", None)
            if publish_latest is not None:
                publish_latest(
                    run_id,
                    manifest_path,
                    status=manifest["status"],
                )
        LOGGER.info("Saved manifest to %s", manifest_path)
        LOGGER.info("Ingestion run %s %s", run_id, manifest["status"])
        return manifest
    except Exception as error:
        completed_at = datetime.now(timezone.utc)
        failure_manifest = {
            "run_id": run_id,
            "status": "failed",
            "started_at": started_at.isoformat(),
            "completed_at": completed_at.isoformat(),
            "duration_seconds": round(
                (completed_at - started_at).total_seconds(), 3
            ),
            "source_counts": source_counts,
            "source_failures": source_failures,
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
