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
RAG_CHUNK_SIZE_CHARS = int(os.getenv("RAG_CHUNK_SIZE_CHARS", "1800"))
RAG_CHUNK_OVERLAP_CHARS = int(
    os.getenv("RAG_CHUNK_OVERLAP_CHARS", "200")
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
    elif STORAGE_MODE == "s3" and not S3_BUCKET_NAME:
        errors.append("S3_BUCKET_NAME is required when STORAGE_MODE=s3")

    return errors


def rag_config_errors():
    errors = []

    if RAG_CHUNK_SIZE_CHARS < 200:
        errors.append("RAG_CHUNK_SIZE_CHARS must be at least 200")
    if RAG_CHUNK_OVERLAP_CHARS < 0:
        errors.append("RAG_CHUNK_OVERLAP_CHARS cannot be negative")
    if RAG_CHUNK_OVERLAP_CHARS >= RAG_CHUNK_SIZE_CHARS:
        errors.append(
            "RAG_CHUNK_OVERLAP_CHARS must be smaller than "
            "RAG_CHUNK_SIZE_CHARS"
        )

    return errors


def _raise_config_errors(errors):
    if errors:
        raise ValueError("Invalid configuration: " + "; ".join(errors))


def validate_storage_config():
    _raise_config_errors(storage_config_errors())


def validate_cleaning_config():
    _raise_config_errors(storage_config_errors() + rag_config_errors())


def validate_config():
    """Fail before fetching with one actionable configuration error."""
    errors = storage_config_errors() + rag_config_errors()

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

    _raise_config_errors(errors)
