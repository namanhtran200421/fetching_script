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

STORAGE_MODE = os.getenv("STORAGE_MODE", "local").lower()
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME")
LOCAL_DATA_DIR = Path(
    os.getenv("LOCAL_DATA_DIR", str(PROJECT_ROOT / "data" / "raw"))
).expanduser()

HTTP_TIMEOUT_SECONDS = float(os.getenv("HTTP_TIMEOUT_SECONDS", "50"))
HTTP_MAX_RETRIES = int(os.getenv("HTTP_MAX_RETRIES", "3"))
HTTP_RETRY_BACKOFF_SECONDS = float(
    os.getenv("HTTP_RETRY_BACKOFF_SECONDS", "1")
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


def validate_config():
    """Fail before fetching with one actionable configuration error."""
    errors = []

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

    if STORAGE_MODE not in {"local", "s3"}:
        errors.append("STORAGE_MODE must be 'local' or 's3'")
    elif STORAGE_MODE == "s3" and not S3_BUCKET_NAME:
        errors.append("S3_BUCKET_NAME is required when STORAGE_MODE=s3")

    if HTTP_TIMEOUT_SECONDS <= 0:
        errors.append("HTTP_TIMEOUT_SECONDS must be greater than zero")
    if HTTP_MAX_RETRIES < 0:
        errors.append("HTTP_MAX_RETRIES cannot be negative")
    if HTTP_RETRY_BACKOFF_SECONDS < 0:
        errors.append("HTTP_RETRY_BACKOFF_SECONDS cannot be negative")

    if errors:
        raise ValueError("Invalid configuration: " + "; ".join(errors))
