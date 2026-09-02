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

    if not document.get("title") and not document.get("content_text"):
        errors.append("title or content_text is required")

    url = document.get("url")
    if url:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            errors.append("url must be an absolute HTTP(S) URL")

    if not isinstance(document.get("metadata"), dict):
        errors.append("metadata must be an object")

    return errors
