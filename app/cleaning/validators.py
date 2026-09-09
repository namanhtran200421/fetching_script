import hashlib
from urllib.parse import urlparse


def validate_document(document):
    errors = []

    for field in (
        "document_id",
        "run_id",
        "source",
        "source_id",
        "content_type",
        "access_class",
    ):
        if document.get(field) in {None, ""}:
            errors.append(f"{field} is required")

    if not isinstance(document.get("content_html"), str):
        errors.append("content_html must be a string")

    if not isinstance(document.get("content_text"), str) or not document.get(
        "content_text"
    ):
        errors.append("content_text is required")

    if not isinstance(document.get("content_markdown"), str) or not document.get(
        "content_markdown"
    ):
        errors.append("content_markdown is required")

    if document.get("content_format") != "text/markdown":
        errors.append("content_format must be text/markdown")

    url = document.get("url")
    if url:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            errors.append("url must be an absolute HTTP(S) URL")

    metadata = document.get("metadata")
    if not isinstance(metadata, dict):
        errors.append("metadata must be an object")
    elif isinstance(document.get("content_markdown"), str):
        expected_hash = hashlib.sha256(
            document["content_markdown"].encode("utf-8")
        ).hexdigest()
        if metadata.get("content_hash") != expected_hash:
            errors.append("metadata.content_hash must match content_markdown")

    return errors
