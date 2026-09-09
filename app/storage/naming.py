import re


def source_file_stem(source_name):
    """Create a predictable, human-readable filename from a source key."""
    value = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(source_name).strip())
    return value.replace("_", "-").strip("-").lower()
