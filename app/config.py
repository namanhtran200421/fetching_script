import os
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent

WORDPRESS_BASE_URL = os.getenv("WORDPRESS_BASE_URL")
WORDPRESS_USERNAME = os.getenv("WORDPRESS_USERNAME")
WORDPRESS_APP_PASSWORD = os.getenv("WORDPRESS_APP_PASSWORD")
MEC_API_TOKEN = os.getenv("MEC_API_TOKEN")


def _boolean_setting(environment_name, default):
    raw_value = os.getenv(environment_name)
    if raw_value is None:
        return default
    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(
        f"{environment_name} must be true or false"
    )


def _integer_allowlist(environment_name):
    raw_value = os.getenv(environment_name, "").strip()
    if not raw_value:
        return ()
    try:
        values = tuple(dict.fromkeys(int(item.strip()) for item in raw_value.split(",")))
    except ValueError as error:
        raise ValueError(
            f"{environment_name} must be a comma-separated list of integer IDs"
        ) from error
    if any(value < 1 for value in values):
        raise ValueError(f"{environment_name} IDs must be positive integers")
    return values


WORDPRESS_PAGE_ALLOWLIST = _integer_allowlist("WORDPRESS_PAGE_ALLOWLIST")

STORAGE_MODE = os.getenv("STORAGE_MODE", "local").lower()
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME")
RAW_S3_BUCKET_NAME = os.getenv("RAW_S3_BUCKET_NAME") or S3_BUCKET_NAME
CLEANED_S3_BUCKET_NAME = os.getenv("CLEANED_S3_BUCKET_NAME") or S3_BUCKET_NAME
LOCAL_DATA_DIR = Path(
    os.getenv("LOCAL_DATA_DIR", str(PROJECT_ROOT / "data" / "raw"))
).expanduser()

HTTP_TIMEOUT_SECONDS = float(os.getenv("HTTP_TIMEOUT_SECONDS", "50"))
HTTP_MAX_RETRIES = int(os.getenv("HTTP_MAX_RETRIES", "3"))
HTTP_RETRY_BACKOFF_SECONDS = float(
    os.getenv("HTTP_RETRY_BACKOFF_SECONDS", "1")
)
HTML_CRAWL_MAX_PAGES = int(os.getenv("HTML_CRAWL_MAX_PAGES", "5000"))
HTML_CRAWL_MAX_SITEMAPS = int(os.getenv("HTML_CRAWL_MAX_SITEMAPS", "100"))
HTML_CRAWL_MAX_REDIRECTS = int(os.getenv("HTML_CRAWL_MAX_REDIRECTS", "5"))
HTML_CRAWL_CONCURRENCY = int(os.getenv("HTML_CRAWL_CONCURRENCY", "6"))
HTML_CRAWL_RESPECT_ROBOTS = _boolean_setting(
    "HTML_CRAWL_RESPECT_ROBOTS", True
)
HTML_CRAWL_MIN_REQUEST_INTERVAL_SECONDS = float(os.getenv(
    "HTML_CRAWL_MIN_REQUEST_INTERVAL_SECONDS", "1"
))
HTML_CRAWL_BURST_SIZE = int(os.getenv(
    "HTML_CRAWL_BURST_SIZE", str(HTML_CRAWL_CONCURRENCY)
))
HTML_CRAWL_MAX_RESPONSE_BYTES = int(
    os.getenv("HTML_CRAWL_MAX_RESPONSE_BYTES", str(5 * 1024 * 1024))
)
INCREMENTAL_INGESTION = _boolean_setting("INCREMENTAL_INGESTION", True)
FULL_RECONCILIATION_INTERVAL_DAYS = int(
    os.getenv("FULL_RECONCILIATION_INTERVAL_DAYS", "7")
)
INCREMENTAL_LOOKBACK_SECONDS = int(
    os.getenv("INCREMENTAL_LOOKBACK_SECONDS", "300")
)


def _calendar_max_events():
    raw_value = os.getenv("MAX_CALENDAR_EVENTS", "").strip().lower()
    if raw_value in {"", "all", "none"}:
        return None

    value = int(raw_value)
    if value < 1:
        raise ValueError("MAX_CALENDAR_EVENTS must be a positive integer or 'all'")
    return value


MAX_CALENDAR_EVENTS = _calendar_max_events()


def storage_config_errors():
    errors = []

    if STORAGE_MODE not in {"local", "s3"}:
        errors.append("STORAGE_MODE must be 'local' or 's3'")
    elif STORAGE_MODE == "s3":
        if not RAW_S3_BUCKET_NAME:
            errors.append(
                "RAW_S3_BUCKET_NAME or S3_BUCKET_NAME is required when STORAGE_MODE=s3"
            )
        if not CLEANED_S3_BUCKET_NAME:
            errors.append(
                "CLEANED_S3_BUCKET_NAME or S3_BUCKET_NAME is required when STORAGE_MODE=s3"
            )

    return errors


def _raise_config_errors(errors):
    if errors:
        raise ValueError("Invalid configuration: " + "; ".join(errors))


def validate_storage_config():
    _raise_config_errors(storage_config_errors())


def validate_cleaning_config():
    _raise_config_errors(storage_config_errors())


def validate_config():
    """Fail before fetching with one actionable configuration error."""
    errors = storage_config_errors()

    required_values = {
        "WORDPRESS_BASE_URL": WORDPRESS_BASE_URL,
        "WORDPRESS_USERNAME": WORDPRESS_USERNAME,
        "WORDPRESS_APP_PASSWORD": WORDPRESS_APP_PASSWORD,
        "MEC_API_TOKEN": MEC_API_TOKEN,
    }
    for name, value in required_values.items():
        if not value:
            errors.append(f"{name} is required")

    if WORDPRESS_BASE_URL:
        parsed_url = urlparse(WORDPRESS_BASE_URL)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            errors.append("WORDPRESS_BASE_URL must be an absolute HTTP(S) URL")

    if HTTP_TIMEOUT_SECONDS <= 0:
        errors.append("HTTP_TIMEOUT_SECONDS must be greater than zero")
    if HTTP_MAX_RETRIES < 0:
        errors.append("HTTP_MAX_RETRIES cannot be negative")
    if HTTP_RETRY_BACKOFF_SECONDS < 0:
        errors.append("HTTP_RETRY_BACKOFF_SECONDS cannot be negative")
    if HTML_CRAWL_MAX_PAGES < 1:
        errors.append("HTML_CRAWL_MAX_PAGES must be a positive integer")
    if HTML_CRAWL_MAX_SITEMAPS < 1:
        errors.append("HTML_CRAWL_MAX_SITEMAPS must be a positive integer")
    if HTML_CRAWL_MAX_REDIRECTS < 0:
        errors.append("HTML_CRAWL_MAX_REDIRECTS cannot be negative")
    if HTML_CRAWL_CONCURRENCY < 1:
        errors.append("HTML_CRAWL_CONCURRENCY must be a positive integer")
    if HTML_CRAWL_MIN_REQUEST_INTERVAL_SECONDS < 0:
        errors.append(
            "HTML_CRAWL_MIN_REQUEST_INTERVAL_SECONDS cannot be negative"
        )
    if HTML_CRAWL_BURST_SIZE < 1:
        errors.append("HTML_CRAWL_BURST_SIZE must be a positive integer")
    if HTML_CRAWL_MAX_RESPONSE_BYTES < 1024:
        errors.append("HTML_CRAWL_MAX_RESPONSE_BYTES must be at least 1024")
    if FULL_RECONCILIATION_INTERVAL_DAYS < 1:
        errors.append(
            "FULL_RECONCILIATION_INTERVAL_DAYS must be a positive integer"
        )
    if INCREMENTAL_LOOKBACK_SECONDS < 0:
        errors.append("INCREMENTAL_LOOKBACK_SECONDS cannot be negative")

    _raise_config_errors(errors)
