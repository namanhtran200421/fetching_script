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


def _slug_list(environment_name, default=()):
    """Read a comma-separated slug list. A set but empty value disables it."""
    raw_value = os.getenv(environment_name)
    if raw_value is None:
        return tuple(default)
    return tuple(dict.fromkeys(
        item.strip().strip("/").lower()
        for item in raw_value.split(",")
        if item.strip().strip("/")
    ))


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

# The public Atlas website is a second, unauthenticated origin. Its settings are
# deliberately separate from the WordPress membership ones so that no membership
# credential or token can ever be selected for it.
ATLAS_PUBLIC_BASE_URL = os.getenv(
    "ATLAS_PUBLIC_BASE_URL", "https://atlas.culturalinfusion.com"
)
# Pages that carry no knowledge-base value: placeholders, sign-in and error
# pages, empty quiz shells, and the FAQ page already ingested by the faq source.
DEFAULT_ATLAS_PAGE_DENYLIST = (
    "faq",
    "test",
    "test-2",
    "page-not-found",
    "sign-in",
    "security-quiz",
    "security-quiz-2",
    # Contentless for retrieval: an external iframe embed and a form-only
    # configuration page. Both clean to an empty document and would be
    # quarantined on every run.
    "deck",
    "survey-config",
)
ATLAS_PAGE_DENYLIST = _slug_list(
    "ATLAS_PAGE_DENYLIST", DEFAULT_ATLAS_PAGE_DENYLIST
)
ATLAS_MEDIA_MIME_TYPES = tuple(dict.fromkeys(
    item.strip().lower()
    for item in os.getenv("ATLAS_MEDIA_MIME_TYPES", "application/pdf").split(",")
    if item.strip()
))
ATLAS_MEDIA_MAX_BYTES = int(
    os.getenv("ATLAS_MEDIA_MAX_BYTES", str(25 * 1024 * 1024))
)
ATLAS_MEDIA_MAX_PDF_PAGES = int(os.getenv("ATLAS_MEDIA_MAX_PDF_PAGES", "500"))

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

    parsed_atlas_url = urlparse(ATLAS_PUBLIC_BASE_URL)
    if (
        parsed_atlas_url.scheme not in {"http", "https"}
        or not parsed_atlas_url.netloc
    ):
        errors.append("ATLAS_PUBLIC_BASE_URL must be an absolute HTTP(S) URL")
    if not ATLAS_MEDIA_MIME_TYPES:
        errors.append("ATLAS_MEDIA_MIME_TYPES must list at least one media type")
    if ATLAS_MEDIA_MAX_BYTES < 1024:
        errors.append("ATLAS_MEDIA_MAX_BYTES must be at least 1024")
    if ATLAS_MEDIA_MAX_PDF_PAGES < 1:
        errors.append("ATLAS_MEDIA_MAX_PDF_PAGES must be a positive integer")

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
