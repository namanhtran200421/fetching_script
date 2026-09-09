import hashlib
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Comment
from markdownify import markdownify


SCHEMA_VERSION = "1.1"
HTML_PARSER = "lxml"
BLOCK_TAGS = {
    "address", "article", "aside", "blockquote", "br", "div", "footer",
    "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "li", "main",
    "nav", "ol", "p", "pre", "section", "table", "td", "th", "tr", "ul",
}
REMOVED_TAGS = {
    "canvas", "footer", "form", "iframe", "nav", "noscript", "script",
    "style", "svg", "template",
}
CLEANING_NOISE_SELECTORS = (
    "header", "[role='navigation']", ".breadcrumb", ".breadcrumbs",
    ".eckb-breadcrumb", ".search",
    ".search-form", ".rating", ".ratings", "[class*='rating']",
    "[class*='feedback']", ".post-navigation", ".navigation", ".nav-links",
    ".cookie-banner", "[class*='cookie']", ".admin-links", ".edit-link",
    ".sharedaddy", ".social-share",
)
SAFE_LINK_SCHEMES = {"http", "https", "mailto", "tel"}


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.ignored_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in REMOVED_TAGS:
            self.ignored_depth += 1
        elif tag in BLOCK_TAGS and not self.ignored_depth:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in REMOVED_TAGS and self.ignored_depth:
            self.ignored_depth -= 1
        elif tag in BLOCK_TAGS and not self.ignored_depth:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.ignored_depth:
            self.parts.append(data)


def clean_plain_text(value):
    if not value:
        return ""
    normalized = str(value).replace("\r\n", "\n").replace("\r", "\n")
    normalized = "".join(
        character
        for character in normalized
        if character in {"\n", "\t"} or ord(character) >= 32
    )
    lines = []
    for line in normalized.splitlines():
        line = " ".join(line.split())
        line = re.sub(r"\s+([,.;:!?])", r"\1", line)
        if line:
            lines.append(line)
    return "\n".join(lines)


def clean_markdown(value):
    if not value:
        return ""
    normalized = str(value).replace("\r\n", "\n").replace("\r", "\n")
    normalized = normalized.replace("\xa0", " ")
    normalized = "".join(
        character
        for character in normalized
        if character in {"\n", "\t"} or ord(character) >= 32
    )
    normalized = re.sub(r"\n[ \t]+\n", "\n\n", normalized)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()


def _safe_reference(value, base_url, allowed_schemes):
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    if value.startswith("#"):
        return value
    resolved = urljoin(base_url, value) if base_url else value
    parsed = urlparse(resolved)
    if parsed.scheme and parsed.scheme.lower() not in allowed_schemes:
        return None
    return resolved


def _sanitized_content(value, base_url=None):
    soup = BeautifulSoup(str(value or ""), HTML_PARSER)
    root = soup.body or soup

    for comment in root.find_all(string=lambda item: isinstance(item, Comment)):
        comment.extract()
    for tag in root.find_all(REMOVED_TAGS):
        tag.decompose()
    for node in reversed(root.select(", ".join(CLEANING_NOISE_SELECTORS))):
        node.decompose()

    # FAQ questions are commonly rendered as accordion buttons. Convert those
    # controls to headings, but unwrap navigation tabs and ordinary CTA buttons.
    # Collapsed panels may be aria-hidden in the UI and must remain in the corpus.
    for node in root.find_all("button"):
        if node.find_parent(("h1", "h2", "h3", "h4", "h5", "h6")):
            node.unwrap()
            continue
        classes = set(node.get("class", ()))
        is_accordion_heading = (
            "x-acc-header" in classes
            or node.find(class_="x-acc-header-text") is not None
        )
        if is_accordion_heading and clean_plain_text(node.get_text(" ", strip=True)):
            # Use a provisional deep level. The Markdown outline normalizer
            # promotes it when no parent section exists and otherwise keeps it
            # beneath the surrounding FAQ/category heading.
            node.name = "h4"
            node.attrs = {}
        else:
            node.unwrap()

    for node in root.select("[role='tab']"):
        node.unwrap()

    for node in root.select("[role='heading']"):
        try:
            level = max(1, min(6, int(node.get("aria-level", 2))))
        except (TypeError, ValueError):
            level = 2
        node.name = f"h{level}"
        node.attrs = {}

    # Page builders often render a visual list as adjacent anchor buttons in a
    # div. Restore the list semantics before Markdown conversion.
    for container in list(root.find_all(("div", "span"))):
        direct_tags = [child for child in container.children if child.name]
        if len(direct_tags) < 2 or any(child.name != "a" for child in direct_tags):
            continue
        unordered_list = soup.new_tag("ul")
        for anchor in direct_tags:
            item = soup.new_tag("li")
            item.append(anchor.extract())
            unordered_list.append(item)
        container.replace_with(unordered_list)

    for anchor in root.find_all("a"):
        href = _safe_reference(
            anchor.get("href"), base_url, SAFE_LINK_SCHEMES
        )
        if href:
            anchor["href"] = href
        else:
            anchor.attrs.pop("href", None)

    for image in root.find_all("img"):
        alt = clean_plain_text(image.get("alt"))
        source = _safe_reference(
            image.get("src"), base_url, {"http", "https"}
        )
        if not alt:
            image.decompose()
        elif source:
            image["src"] = source
        else:
            image.replace_with(alt)

    return root


def _title_like_heading(value):
    if not value or len(value) > 120 or value.endswith((".", "!", "?")):
        return False
    words = re.findall(r"[A-Za-z][A-Za-z'’-]*", value)
    if not words:
        return False
    minor_words = {
        "a", "an", "and", "as", "at", "by", "for", "from", "in", "into",
        "of", "on", "or", "the", "to", "vs", "with",
    }
    return all(
        word.casefold() in minor_words
        or word.isupper()
        or word[0].isupper()
        for word in words
    )


def _promote_research_markdown_headings(value):
    output = []
    in_fence = False
    pattern = re.compile(r"^((\d+(?:\.\d+)*)(?:[.)])?)\s+(.+)$")
    for line in value.splitlines():
        if line.strip().startswith(("```", "~~~")):
            in_fence = not in_fence
            output.append(line)
            continue
        if in_fence or line.startswith(("#", "- ", "* ", "> ", "|")):
            output.append(line)
            continue
        match = pattern.match(line.strip())
        if not match:
            output.append(line)
            continue

        display_number, section_number, remainder = match.groups()
        heading_level = min(6, 2 + section_number.count("."))
        if _title_like_heading(remainder):
            output.append(
                f"{'#' * heading_level} {display_number} {remainder}"
            )
            continue

        words = remainder.split()
        uppercase_prefix = []
        while words:
            letters = re.sub(r"[^A-Za-z]", "", words[0])
            if not letters or not letters.isupper():
                break
            uppercase_prefix.append(words.pop(0))
        heading = " ".join(uppercase_prefix)
        body = " ".join(words)
        if len(re.sub(r"[^A-Za-z]", "", heading)) >= 4 and len(body) >= 20:
            output.extend([
                f"{'#' * heading_level} {display_number} {heading}",
                "",
                body,
            ])
        else:
            output.append(line)
    return clean_markdown("\n".join(output))


def html_representations(
    value,
    base_url=None,
    infer_research_headings=False,
):
    root = _sanitized_content(value, base_url=base_url)
    sanitized_html = str(root)

    parser = _TextExtractor()
    # HTMLParser resolves character references itself. Pre-unescaping would turn
    # escaped examples such as &lt;script&gt; into markup and lose valid text.
    parser.feed(sanitized_html)
    text_lines = [
        " ".join(line.split())
        for line in "".join(parser.parts).splitlines()
    ]
    content_text = clean_plain_text(
        "\n".join(line for line in text_lines if line)
    )
    content_markdown = clean_markdown(markdownify(
        sanitized_html,
        heading_style="ATX",
        bullets="-",
        bs4_options=HTML_PARSER,
    ))
    if infer_research_headings:
        content_markdown = _promote_research_markdown_headings(content_markdown)
    return content_text, content_markdown


def html_to_text(value):
    return html_representations(value)[0]


def html_to_markdown(value, base_url=None, infer_research_headings=False):
    return html_representations(
        value,
        base_url=base_url,
        infer_research_headings=infer_research_headings,
    )[1]


def _markdown_label(value):
    value = re.sub(r"^#{1,6}\s+", "", value.strip())
    value = re.sub(r"\\([\\`*_{}\[\]()#+\-.!|>])", r"\1", value)
    return re.sub(r"[`*_]", "", value).strip()


def _escape_markdown_inline(value):
    return re.sub(r"([\\`*_\[\]<>])", r"\\\1", value)


def _normalize_body_headings(lines):
    headings = []
    in_fence = False
    for index, line in enumerate(lines):
        if line.strip().startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = re.match(r"^(#{1,6})\s+(.+)$", line.strip())
        if match:
            headings.append((index, len(match.group(1)), match.group(2)))
    if not headings:
        return lines

    shift = 2 - min(level for _, level, _ in headings)
    previous_level = 1
    for index, level, label in headings:
        level = max(2, min(6, level + shift))
        level = min(level, previous_level + 1)
        lines[index] = f"{'#' * level} {label}"
        previous_level = level
    return lines


def with_document_title(content_markdown, title):
    title = clean_plain_text(title).replace("\n", " ")
    content_markdown = clean_markdown(content_markdown)
    if not title:
        return content_markdown

    title_heading = f"# {_escape_markdown_inline(title)}"
    if not content_markdown:
        return title_heading

    lines = content_markdown.splitlines()
    body_lines = []
    in_fence = False
    for line in lines:
        if line.strip().startswith(("```", "~~~")):
            in_fence = not in_fence
            body_lines.append(line)
            continue
        is_heading = (
            not in_fence
            and re.match(r"^#{1,6}\s+", line.strip()) is not None
        )
        if is_heading and _markdown_label(line).casefold() == title.casefold():
            continue
        body_lines.append(line)

    first_content_index = next(
        (index for index, line in enumerate(body_lines) if line.strip()),
        None,
    )
    if first_content_index is not None:
        first_line = body_lines[first_content_index]
        if _markdown_label(first_line).casefold() == title.casefold():
            del body_lines[first_content_index]

    body_lines = _normalize_body_headings(body_lines)
    body_markdown = clean_markdown("\n".join(body_lines))
    return (
        f"{title_heading}\n\n{body_markdown}"
        if body_markdown else title_heading
    )


def rendered(value):
    if isinstance(value, dict):
        return value.get("rendered", "")
    return value or ""


def _content_hash(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalize_wordpress_record(record, source_name, source_config, run_id):
    title_html = rendered(record.get("title"))
    content_html = rendered(record.get("content_html")) or rendered(
        record.get("content")
    )
    source_id = record.get("id")
    title = html_to_text(title_html)
    content_text, body_markdown = html_representations(
        content_html,
        base_url=record.get("link"),
        infer_research_headings=source_config["content_type"] == "research",
    )
    content_markdown = with_document_title(body_markdown, title)

    return {
        "schema_version": SCHEMA_VERSION,
        "document_id": f"{source_name}:{source_id}",
        "run_id": run_id,
        "source": source_name,
        "source_id": source_id,
        "content_type": source_config["content_type"],
        "access_class": source_config["access_class"],
        "title": title,
        "content_format": "text/markdown",
        "content_html": content_html,
        "content_text": content_text,
        "content_markdown": content_markdown,
        "modified_at": (
            record.get("modified_gmt") or record.get("modified")
            or record.get("updated_at")
        ),
        "url": record.get("link"),
        "metadata": {
            "categories": record.get("mec_category", []),
            "content_protected": (
                record.get("content", {}).get("protected", False)
                if isinstance(record.get("content"), dict)
                else False
            ),
            "source_type": record.get("source_type", source_config["content_type"]),
            "source_url": record.get("source_url") or record.get("link"),
            "canonical_url": record.get("canonical_url") or record.get("link"),
            "acquisition_method": record.get(
                "acquisition_method", source_config.get("acquisition_method", "rest")
            ),
            "retrieved_at": record.get("retrieved_at"),
            "published_at": record.get("published_at"),
            "updated_at": record.get("updated_at"),
            "content_hash": _content_hash(content_markdown),
            "discovered_via": record.get("discovered_via"),
            "sitemap_url": record.get("sitemap_url"),
            "sitemap_lastmod": record.get("sitemap_lastmod"),
            "wordpress_id": record.get("wordpress_id"),
            "wordpress_subtype": record.get("wordpress_subtype"),
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
            details.append((label, clean_plain_text(value)))

    description_text, description_markdown = html_representations(description_html)
    detail_lines = [f"{label}: {value}" for label, value in details]
    content_text = clean_plain_text("\n".join(
        part for part in [description_text, *detail_lines] if part
    ))
    details_markdown = "\n".join(
        f"- **{_escape_markdown_inline(label)}:** {_escape_markdown_inline(value)}"
        for label, value in details
    )
    body_markdown = description_markdown
    if details_markdown:
        body_markdown = "\n\n".join(
            part for part in (
                body_markdown,
                f"## Membership details\n\n{details_markdown}",
            )
            if part
        )
    content_markdown = with_document_title(body_markdown, name)

    return {
        "schema_version": SCHEMA_VERSION,
        "document_id": f"membership_plans:{source_id}",
        "run_id": run_id,
        "source": "membership_plans",
        "source_id": source_id,
        "content_type": "membership_plan",
        "access_class": "configuration",
        "title": name,
        "content_format": "text/markdown",
        "content_html": description_html,
        "content_text": content_text,
        "content_markdown": content_markdown,
        "modified_at": None,
        "url": None,
        "metadata": {
            **{
                key: value
                for key, value in record.items()
                if key not in {"id", "name", "description", "confirmation"}
            },
            "content_hash": _content_hash(content_markdown),
        },
    }


def normalize_calendar_event(record, run_id):
    event_id = record.get("event_id")
    response = record.get("response") or {}
    occurrences = response.get("occurrences") or []
    lines = []

    def format_boundary(value):
        if not isinstance(value, dict):
            return value
        date = value.get("date")
        time_parts = [
            value.get("hour"), value.get("minutes"), value.get("ampm")
        ]
        time_value = ":".join(
            str(part) for part in time_parts[:2] if part not in {None, ""}
        )
        if time_parts[2] not in {None, ""}:
            time_value = f"{time_value} {time_parts[2]}".strip()
        return " ".join(
            str(part) for part in (date, time_value) if part
        )

    for occurrence in occurrences:
        if not isinstance(occurrence, dict):
            continue
        start = format_boundary(occurrence.get("start"))
        end = format_boundary(occurrence.get("end"))
        if start or end:
            lines.append(f"Start: {start or 'unknown'}; End: {end or 'unknown'}")

    title = f"Calendar schedule for event {event_id}"
    content_text = clean_plain_text("\n".join(lines))
    occurrence_markdown = "\n".join(
        f"- {_escape_markdown_inline(line)}" for line in lines
    )
    body_markdown = (
        f"## Occurrences\n\n{occurrence_markdown}"
        if occurrence_markdown else ""
    )
    content_markdown = with_document_title(body_markdown, title)

    return {
        "schema_version": SCHEMA_VERSION,
        "document_id": f"calendar_occurrences:{event_id}",
        "run_id": run_id,
        "source": "calendar_occurrences",
        "source_id": event_id,
        "content_type": "calendar_occurrence_collection",
        "access_class": "member_restricted",
        "title": title,
        "content_format": "text/markdown",
        "content_html": "",
        "content_text": content_text,
        "content_markdown": content_markdown,
        "modified_at": None,
        "url": None,
        "metadata": {
            "api_success": response.get("success"),
            "derived_from": response.get("derived_from"),
            "occurrences": occurrences,
            "content_hash": _content_hash(content_markdown),
        },
    }
