import html
from html.parser import HTMLParser


SCHEMA_VERSION = "1.0"
BLOCK_TAGS = {
    "address", "article", "aside", "blockquote", "br", "div", "footer",
    "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "li", "main",
    "nav", "ol", "p", "pre", "section", "table", "td", "th", "tr", "ul",
}
IGNORED_TAGS = {"script", "style", "noscript", "svg"}


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.ignored_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in IGNORED_TAGS:
            self.ignored_depth += 1
        elif tag in BLOCK_TAGS and not self.ignored_depth:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in IGNORED_TAGS and self.ignored_depth:
            self.ignored_depth -= 1
        elif tag in BLOCK_TAGS and not self.ignored_depth:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.ignored_depth:
            self.parts.append(data)


def html_to_text(value):
    if not value:
        return ""

    parser = _TextExtractor()
    parser.feed(html.unescape(str(value)))
    lines = [" ".join(line.split()) for line in "".join(parser.parts).splitlines()]
    return "\n".join(line for line in lines if line)


def rendered(value):
    if isinstance(value, dict):
        return value.get("rendered", "")
    return value or ""


def normalize_wordpress_record(record, source_name, source_config, run_id):
    title_html = rendered(record.get("title"))
    content_html = rendered(record.get("content"))
    source_id = record.get("id")

    return {
        "schema_version": SCHEMA_VERSION,
        "document_id": f"{source_name}:{source_id}",
        "run_id": run_id,
        "source": source_name,
        "source_id": source_id,
        "content_type": source_config["content_type"],
        "access_class": source_config["access_class"],
        "title": html_to_text(title_html),
        "content_html": content_html,
        "content_text": html_to_text(content_html),
        "modified_at": record.get("modified_gmt") or record.get("modified"),
        "url": record.get("link"),
        "metadata": {
            "categories": record.get("mec_category", []),
            "content_protected": (
                record.get("content", {}).get("protected", False)
                if isinstance(record.get("content"), dict)
                else False
            ),
        },
    }


def normalize_membership_plan(plan_id, record, run_id):
    source_id = record.get("id", plan_id)
    description_html = record.get("description", "")
    name = html_to_text(record.get("name", ""))
    details = []

    for label, key in (
        ("Initial payment", "initial_payment"),
        ("Billing amount", "billing_amount"),
        ("Billing cycle", "cycle_period"),
        ("Expiration period", "expiration_period"),
    ):
        value = record.get(key)
        if value not in {None, ""}:
            details.append(f"{label}: {value}")

    description_text = html_to_text(description_html)
    content_text = "\n".join(
        part for part in [description_text, *details] if part
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "document_id": f"membership_plans:{source_id}",
        "run_id": run_id,
        "source": "membership_plans",
        "source_id": source_id,
        "content_type": "membership_plan",
        "access_class": "configuration",
        "title": name,
        "content_html": description_html,
        "content_text": content_text,
        "modified_at": None,
        "url": None,
        "metadata": {
            key: value
            for key, value in record.items()
            if key not in {"id", "name", "description", "confirmation"}
        },
    }


def normalize_calendar_event(record, run_id):
    event_id = record.get("event_id")
    response = record.get("response") or {}
    occurrences = response.get("occurrences") or []
    lines = []

    for occurrence in occurrences:
        if not isinstance(occurrence, dict):
            continue
        start = occurrence.get("start")
        end = occurrence.get("end")
        if start or end:
            lines.append(f"Start: {start or 'unknown'}; End: {end or 'unknown'}")

    return {
        "schema_version": SCHEMA_VERSION,
        "document_id": f"calendar_occurrences:{event_id}",
        "run_id": run_id,
        "source": "calendar_occurrences",
        "source_id": event_id,
        "content_type": "calendar_occurrence_collection",
        "access_class": "member_restricted",
        "title": f"Calendar schedule for event {event_id}",
        "content_html": "",
        "content_text": "\n".join(lines),
        "modified_at": None,
        "url": None,
        "metadata": {
            "api_success": response.get("success"),
            "occurrences": occurrences,
        },
    }
