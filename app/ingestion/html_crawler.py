import asyncio
import fnmatch
import hashlib
import logging
import posixpath
import time
from datetime import datetime, timezone
from functools import lru_cache
from urllib.parse import unquote, urldefrag, urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser
from xml.etree import ElementTree

from bs4 import BeautifulSoup

from app.clients.http_client import AsyncRetryingHttpClient


LOGGER = logging.getLogger(__name__)
HTML_CONTENT_TYPES = {"text/html", "application/xhtml+xml"}
SITEMAP_CONTENT_TYPES = {
    "application/xml",
    "text/xml",
    "application/rss+xml",
}
REMOVED_TAGS = {"script", "style", "noscript", "nav", "form", "footer"}
NOISE_SELECTORS = (
    "header", "[role='navigation']", ".breadcrumb", ".breadcrumbs",
    ".eckb-breadcrumb", ".search", ".search-form", ".rating", ".ratings",
    "[class*='rating']", "[class*='feedback']", ".post-navigation",
    ".navigation", ".nav-links", ".cookie-banner", "[class*='cookie']",
    ".admin-links", ".edit-link", ".sharedaddy", ".social-share",
)
ACCESS_DENIED_MARKERS = (
    "membership required", "you must be a member", "access denied",
    "you do not have permission", "please log in", "already a member? log in",
)
ERROR_TITLE_MARKERS = ("404", "not found", "access denied", "forbidden", "log in")
EMPTY_PAGE_MARKERS = (
    "nothing to show right now",
    "whatever you were looking for is no longer here",
)
HTML_PARSER = "lxml"


class CrawlPageRejected(ValueError):
    pass


class CrawlPageSkipped(CrawlPageRejected):
    pass


def reject_access_or_error_page(soup):
    page_text = " ".join(soup.get_text(" ", strip=True).lower().split())
    body_classes = " ".join(soup.body.get("class", [])) if soup.body else ""
    title_text = soup.title.get_text(" ", strip=True).lower() if soup.title else ""
    if any(marker in page_text for marker in EMPTY_PAGE_MARKERS):
        raise CrawlPageSkipped("empty archive or no-results page")
    if (
        "pmpro-no-access" in body_classes
        or any(marker in page_text for marker in ACCESS_DENIED_MARKERS)
        or any(marker in title_text for marker in ERROR_TITLE_MARKERS)
    ):
        raise CrawlPageRejected("login, access-denied, or error page")


def _host_key(parsed):
    try:
        port = parsed.port
    except ValueError:
        return "", -1
    if port is None:
        port = 443 if parsed.scheme.lower() == "https" else 80
    return parsed.hostname.lower() if parsed.hostname else "", port


def normalize_crawl_url(candidate, base_url, approved_base_url, path_prefix):
    """Return a stable approved crawl URL, or None for an unsafe link."""
    if not candidate or not isinstance(candidate, str):
        return None
    candidate = candidate.strip()
    if not candidate or candidate.startswith(("#", "//")):
        return None
    candidate_parts = urlsplit(candidate)
    resolution_base = (
        approved_base_url
        if candidate.startswith("/") or candidate_parts.scheme
        else base_url
    )
    return _normalize_crawl_url_cached(
        candidate,
        resolution_base,
        approved_base_url,
        path_prefix,
    )


@lru_cache(maxsize=32768)
def _normalize_crawl_url_cached(
    candidate,
    base_url,
    approved_base_url,
    path_prefix,
):
    joined, _ = urldefrag(urljoin(base_url, candidate))
    parsed = urlsplit(joined)
    approved = urlsplit(approved_base_url)
    if parsed.scheme.lower() not in {"http", "https"}:
        return None
    if parsed.username or parsed.password or _host_key(parsed) != _host_key(approved):
        return None

    try:
        decoded_path = unquote(parsed.path)
    except (UnicodeDecodeError, ValueError):
        return None
    if "\\" in decoded_path or "\x00" in decoded_path:
        return None
    path = posixpath.normpath("/" + decoded_path.lstrip("/"))
    if decoded_path.endswith("/") or not posixpath.splitext(path)[1]:
        path = path.rstrip("/") + "/"

    clean_prefix = path_prefix.strip("/")
    prefix = f"/{clean_prefix}/" if clean_prefix else "/"
    if path != prefix and not path.startswith(prefix):
        return None

    # Queries do not identify articles and can create pagination/tracking loops.
    return urlunsplit((approved.scheme.lower(), approved.netloc.lower(), path, "", ""))


def _xml_local_name(tag):
    return tag.rsplit("}", 1)[-1].lower()


def parse_sitemap_xml(content, max_bytes=5 * 1024 * 1024):
    """Parse a bounded sitemap without resolving entities or external data."""
    if not content:
        raise CrawlPageRejected("sitemap is empty")
    if len(content) > max_bytes:
        raise CrawlPageRejected("sitemap exceeds the configured size limit")
    upper_prefix = content[:4096].upper()
    if b"<!DOCTYPE" in upper_prefix or b"<!ENTITY" in upper_prefix:
        raise CrawlPageRejected("sitemap contains a prohibited XML declaration")
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError as error:
        raise CrawlPageRejected(f"sitemap XML is invalid: {error}") from error

    document_type = _xml_local_name(root.tag)
    if document_type not in {"sitemapindex", "urlset"}:
        raise CrawlPageRejected(
            f"unsupported sitemap document type: {document_type}"
        )

    entry_type = "sitemap" if document_type == "sitemapindex" else "url"
    entries = []
    for node in root:
        if _xml_local_name(node.tag) != entry_type:
            continue
        location = None
        last_modified = None
        for child in node:
            name = _xml_local_name(child.tag)
            value = child.text.strip() if child.text else ""
            if name == "loc":
                location = value
            elif name == "lastmod":
                last_modified = value or None
        if location:
            entries.append((location, last_modified))
    if not entries:
        raise CrawlPageRejected("sitemap contains no locations")
    return document_type, entries


def parse_html(html):
    return BeautifulSoup(html, HTML_PARSER)


def parse_and_validate_html(html):
    soup = parse_html(html)
    reject_access_or_error_page(soup)
    return soup


def _as_soup(html_or_soup):
    if isinstance(html_or_soup, BeautifulSoup):
        return html_or_soup
    return parse_html(html_or_soup)


def discover_links(html_or_soup, response_url, approved_base_url, path_prefix):
    soup = _as_soup(html_or_soup)
    links = set()
    # Navigation/footer links repeat across most WordPress pages. Normalize each
    # distinct href once, then let normalize_crawl_url's bounded cache reuse the
    # result across pages.
    hrefs = {anchor.get("href") for anchor in soup.find_all("a", href=True)}
    for href in hrefs:
        normalized = normalize_crawl_url(
            href, response_url, approved_base_url, path_prefix
        )
        if normalized:
            links.add(normalized)
    return links


def _structured_text(
    node,
    extra_block_tags=(),
    structured_text_selectors=(),
):
    for br in node.find_all("br"):
        br.replace_with("\n")
    if structured_text_selectors:
        selected = node.select(", ".join(structured_text_selectors))
        if selected:
            selected_ids = {id(item) for item in selected}
            parts = []
            for block in selected:
                # If both a semantic container and one of its children match,
                # keep the container once instead of duplicating its content.
                if any(id(parent) in selected_ids for parent in block.parents):
                    continue
                text = " ".join(block.get_text(" ", strip=True).split())
                if text and (not parts or text != parts[-1]):
                    parts.append(text)
            if parts:
                return "\n".join(parts)
    block_tags = (
        "h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "blockquote",
        "tr", "pre",
    )
    parts = []
    for block in node.find_all(block_tags + tuple(extra_block_tags)):
        text = " ".join(block.get_text(" ", strip=True).split())
        if text and (not parts or text != parts[-1]):
            parts.append(text)
    if not parts:
        text = node.get_text("\n", strip=True)
        parts = [" ".join(line.split()) for line in text.splitlines() if line.strip()]
    return "\n".join(parts)


def _displayed_date(soup, selectors):
    for selector in selectors:
        node = soup.select_one(selector)
        if not node:
            continue
        value = node.get("datetime") if node.name == "time" else None
        value = value or node.get_text(" ", strip=True)
        value = " ".join(value.split()) if value else ""
        if value:
            return value
    return None


def extract_article(
    html_or_soup,
    final_url,
    approved_base_url,
    source_name,
    config,
    retrieved_at=None,
    already_validated=False,
):
    soup = _as_soup(html_or_soup)
    if not already_validated:
        reject_access_or_error_page(soup)

    canonical_node = soup.find("link", rel=lambda value: value and "canonical" in value)
    if canonical_node and canonical_node.get("href"):
        canonical_url = normalize_crawl_url(
            canonical_node["href"], final_url, approved_base_url,
            config["allowed_path_prefix"],
        )
        if not canonical_url:
            raise CrawlPageRejected("canonical URL is invalid or outside the approved source")
    else:
        canonical_url = normalize_crawl_url(
            final_url, final_url, approved_base_url, config["allowed_path_prefix"]
        )
    if not canonical_url:
        raise CrawlPageRejected("final URL is outside the approved source")

    title_node = None
    for selector in (
        ".eckb-article-title",
        "#eckb-article-content-title-container h1",
        "article h1",
        "main h1",
        "h1",
    ):
        title_node = soup.select_one(selector)
        if title_node:
            break
    if title_node:
        title = " ".join(title_node.get_text(" ", strip=True).split())
    else:
        title = " ".join((soup.title.get_text(" ", strip=True) if soup.title else "").split())
    if not title:
        raise CrawlPageRejected("article title is missing")

    content = None
    for selector in config.get("article_selectors", ()):
        content = soup.select_one(selector)
        if content:
            break
    if content is None:
        content = soup.find("article")
    if content is None:
        content = soup.find("main")
    if content is None and config.get("allow_body_fallback"):
        content = soup.body
    if content is None:
        raise CrawlPageRejected("article body is missing")

    # Preserve the selected source HTML before applying the temporary cleanup
    # needed for fetch-time validation and deduplication. Semantic conversion is
    # owned by the cleaning stage, which can now be rerun without recrawling.
    content_html = str(content)

    for tag in content.find_all(REMOVED_TAGS):
        tag.decompose()
    # A combined selector is one tree traversal rather than one per selector.
    # Reverse order safely removes descendants before selected ancestors.
    for node in reversed(content.select(", ".join(NOISE_SELECTORS))):
        node.decompose()

    content_text = _structured_text(
        content,
        extra_block_tags=config.get("structured_text_tags", ()),
        structured_text_selectors=config.get(
            "structured_text_selectors", ()
        ),
    )
    minimum = config.get("minimum_content_characters", 80)
    if len(content_text) < minimum:
        raise CrawlPageRejected(
            "article body is empty or suspiciously short (%d characters)" % len(content_text)
        )

    retrieved_at = retrieved_at or datetime.now(timezone.utc).isoformat()
    content_hash = hashlib.sha256(content_text.encode("utf-8")).hexdigest()
    source_id = hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()[:24]
    published_at = _displayed_date(
        soup, (
            ".eckb-article-content-created-date-container time",
            "time.published",
            ".published time",
            ".post-date time",
            ".date.published",
        )
    )
    updated_at = _displayed_date(
        soup, (
            ".eckb-article-content-last-updated-date-container time",
            "time.updated",
            ".updated time",
            ".last-updated time",
            ".date.updated",
        )
    )
    return {
        "id": source_id,
        "title": title,
        "content_html": content_html,
        "link": canonical_url,
        "canonical_url": canonical_url,
        "source_url": final_url,
        "source_type": source_name,
        "acquisition_method": "html",
        "retrieved_at": retrieved_at,
        "published_at": published_at,
        "updated_at": updated_at,
        "content_hash": content_hash,
    }


class HtmlCrawler:
    def __init__(
        self,
        base_url,
        http_client=None,
        auth=None,
        max_pages=5000,
        max_redirects=5,
        concurrency=6,
        max_response_bytes=5 * 1024 * 1024,
        timeout=50,
        max_retries=3,
        backoff_seconds=1,
        max_sitemaps=100,
        respect_robots=False,
        minimum_request_interval=0,
        burst_size=None,
        sleep=asyncio.sleep,
        clock=time.monotonic,
        user_agent=(
            "CulturalInfusionAtlasIngestion/1.0 "
            "(+https://culturalinfusion.com)"
        ),
    ):
        self.base_url = base_url.rstrip("/")
        self.http_client = http_client
        self.auth = auth
        self.max_pages = max_pages
        self.max_redirects = max_redirects
        self.concurrency = concurrency
        self.max_response_bytes = max_response_bytes
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds
        self.max_sitemaps = max_sitemaps
        self.respect_robots = respect_robots
        self.minimum_request_interval = minimum_request_interval
        self.burst_size = max(1, burst_size or concurrency)
        self.user_agent = user_agent
        self.sleep = sleep
        self.clock = clock
        self._robots_loaded = False
        self._robots_parser = None
        self._robots_crawl_delay = None
        self._burst_allowed = not respect_robots
        self._effective_request_interval = minimum_request_interval
        self._next_request_at = 0.0
        self._tokens = float(self.burst_size)
        self._token_updated_at = self.clock()
        self._rate_lock = None
        self._sitemap_index = None
        self._sitemap_documents = {}
        self.headers = {
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml",
        }

    async def _load_robots_policy(self):
        if self._robots_loaded or not self.respect_robots:
            return
        robots_url = self.base_url + "/robots.txt"
        try:
            response = await self.http_client.get(
                robots_url,
                auth=self.auth,
                headers={**self.headers, "Accept": "text/plain"},
                follow_redirects=False,
                accepted_status_codes={404},
            )
            parser = RobotFileParser()
            parser.set_url(robots_url)
            if response.status_code == 200:
                parser.parse(response.text.splitlines())
                crawl_delay = parser.crawl_delay(self.user_agent)
                if crawl_delay is None:
                    crawl_delay = parser.crawl_delay("*")
                if crawl_delay is not None:
                    self._robots_crawl_delay = float(crawl_delay)
                    self._effective_request_interval = max(
                        self.minimum_request_interval,
                        self._robots_crawl_delay,
                    )
                else:
                    self._burst_allowed = True
                self._robots_parser = parser
            else:
                LOGGER.warning(
                    "robots.txt returned HTTP %s; applying only the configured "
                    "minimum request interval",
                    response.status_code,
                )
        except Exception as error:
            LOGGER.warning(
                "Could not read robots.txt; applying only the configured "
                "minimum request interval: %s",
                error,
            )
        self._robots_loaded = True
        if not self._burst_allowed and self._effective_request_interval > 0:
            self._next_request_at = max(
                self._next_request_at,
                self.clock() + self._effective_request_interval,
            )
            LOGGER.info(
                "HTML crawl uses strict request spacing of %.1f seconds",
                self._effective_request_interval,
            )
        elif self._effective_request_interval > 0:
            LOGGER.info(
                "HTML crawl uses a %.2f requests/second fallback token bucket "
                "with burst %d",
                1 / self._effective_request_interval,
                self.burst_size,
            )

    async def _acquire_request_slot(self):
        if self._effective_request_interval <= 0:
            return
        if self._rate_lock is None:
            self._rate_lock = asyncio.Lock()

        # Crawl-delay is a strict inter-request spacing instruction. A burst
        # would violate it, so keep the existing start-time gate for hosts that
        # publish one.
        if not self._burst_allowed:
            async with self._rate_lock:
                delay = self._next_request_at - self.clock()
                if delay > 0:
                    await self.sleep(delay)
                self._next_request_at = (
                    self.clock() + self._effective_request_interval
                )
            return

        # Without a published crawl delay, a token bucket preserves the same
        # configured average request rate while allowing bounded concurrency.
        rate = 1 / self._effective_request_interval
        while True:
            async with self._rate_lock:
                now = self.clock()
                elapsed = max(0.0, now - self._token_updated_at)
                self._tokens = min(
                    float(self.burst_size),
                    self._tokens + elapsed * rate,
                )
                self._token_updated_at = now
                if self._tokens >= 1:
                    self._tokens -= 1
                    return
                delay = (1 - self._tokens) / rate
            await self.sleep(delay)

    async def _request(self, url, **kwargs):
        if (
            self._robots_parser is not None
            and not self._robots_parser.can_fetch(self.user_agent, url)
        ):
            raise CrawlPageRejected("robots.txt disallows this URL")
        await self._acquire_request_slot()
        return await self.http_client.get(url, **kwargs)

    async def _get(self, url, path_prefix, conditional_headers=None):
        current = url
        for redirect_number in range(self.max_redirects + 1):
            response = await self._request(
                current,
                auth=self.auth,
                headers={**self.headers, **(conditional_headers or {})},
                follow_redirects=False,
                accepted_status_codes={304},
            )
            if response.status_code not in {301, 302, 303, 307, 308}:
                final_url = normalize_crawl_url(
                    str(response.url), current, self.base_url, path_prefix
                )
                if not final_url:
                    raise CrawlPageRejected(
                        "response resolved outside the approved source"
                    )
                if response.status_code == 304:
                    return response, final_url
                media_type = response.headers.get(
                    "content-type", ""
                ).split(";", 1)[0].lower()
                if media_type not in HTML_CONTENT_TYPES:
                    raise CrawlPageRejected(
                        "response is not successful HTML"
                    )
                content_length = response.headers.get("content-length")
                if content_length:
                    try:
                        if int(content_length) > self.max_response_bytes:
                            raise CrawlPageRejected(
                                "HTML response exceeds the configured size limit"
                            )
                    except ValueError:
                        pass
                if len(response.content) > self.max_response_bytes:
                    raise CrawlPageRejected(
                        "HTML response exceeds the configured size limit"
                    )
                return response, final_url
            if redirect_number == self.max_redirects:
                raise CrawlPageRejected("too many redirects")
            target = normalize_crawl_url(
                response.headers.get("location"),
                current,
                self.base_url,
                path_prefix,
            )
            if not target:
                raise CrawlPageRejected(
                    "redirect target is outside the approved source"
                )
            current = target
        raise CrawlPageRejected("redirect handling failed")

    async def _get_sitemap_document(self, url):
        current = url
        for redirect_number in range(self.max_redirects + 1):
            response = await self._request(
                current,
                auth=self.auth,
                headers={**self.headers, "Accept": "application/xml,text/xml"},
                follow_redirects=False,
                # The live site currently serves valid sitemap XML with 404.
                # Accept that one status only, then validate the XML strictly.
                accepted_status_codes={404},
            )
            if response.status_code not in {301, 302, 303, 307, 308}:
                final_url = normalize_crawl_url(
                    str(response.url), current, self.base_url, "/"
                )
                if not final_url:
                    raise CrawlPageRejected(
                        "sitemap response resolved outside the approved host"
                    )
                media_type = response.headers.get(
                    "content-type", ""
                ).split(";", 1)[0].lower()
                if media_type not in SITEMAP_CONTENT_TYPES:
                    raise CrawlPageRejected("sitemap response is not XML")
                document_type, entries = parse_sitemap_xml(
                    response.content,
                    max_bytes=self.max_response_bytes,
                )
                return final_url, document_type, entries
            if redirect_number == self.max_redirects:
                raise CrawlPageRejected("too many sitemap redirects")
            target = normalize_crawl_url(
                response.headers.get("location"),
                current,
                self.base_url,
                "/",
            )
            if not target:
                raise CrawlPageRejected(
                    "sitemap redirect target is outside the approved host"
                )
            current = target
        raise CrawlPageRejected("sitemap redirect handling failed")

    async def _load_sitemap_index(self, sitemap_path):
        if self._sitemap_index is not None:
            return self._sitemap_index

        root_url = normalize_crawl_url(
            sitemap_path,
            self.base_url + "/",
            self.base_url,
            "/",
        )
        if not root_url:
            raise CrawlPageRejected("sitemap path is outside the approved host")
        final_url, document_type, entries = await self._get_sitemap_document(
            root_url
        )
        if document_type == "urlset":
            self._sitemap_documents[final_url] = entries
            self._sitemap_index = (final_url,)
            return self._sitemap_index

        sitemap_urls = []
        for candidate, _ in entries:
            normalized = normalize_crawl_url(
                candidate,
                final_url,
                self.base_url,
                "/",
            )
            if normalized and normalized not in sitemap_urls:
                sitemap_urls.append(normalized)
        if not sitemap_urls:
            raise CrawlPageRejected(
                "sitemap index contains no approved child sitemaps"
            )
        if len(sitemap_urls) > self.max_sitemaps:
            raise CrawlPageRejected(
                "sitemap index exceeds HTML_CRAWL_MAX_SITEMAPS=%d"
                % self.max_sitemaps
            )
        self._sitemap_index = tuple(sitemap_urls)
        return self._sitemap_index

    @staticmethod
    def _sitemap_is_selected(url, include_patterns, exclude_patterns):
        file_name = posixpath.basename(urlsplit(url).path)
        return (
            any(fnmatch.fnmatchcase(file_name, pattern) for pattern in include_patterns)
            and not any(
                fnmatch.fnmatchcase(file_name, pattern)
                for pattern in exclude_patterns
            )
        )

    async def _discover_from_sitemap(self, config):
        include_patterns = tuple(config.get("sitemap_include", ()))
        if not include_patterns:
            return {}, 0, 0
        exclude_patterns = tuple(config.get("sitemap_exclude", ()))
        sitemap_urls = await self._load_sitemap_index(
            config.get("sitemap_path", "/wp-sitemap.xml")
        )
        selected = [
            url for url in sitemap_urls
            if self._sitemap_is_selected(
                url, include_patterns, exclude_patterns
            )
        ]
        if not selected:
            raise CrawlPageRejected(
                "no child sitemaps matched the configured patterns"
            )

        semaphore = asyncio.Semaphore(self.concurrency)

        async def load_one(sitemap_url):
            if sitemap_url in self._sitemap_documents:
                return sitemap_url, self._sitemap_documents[sitemap_url], None
            async with semaphore:
                try:
                    final_url, document_type, entries = (
                        await self._get_sitemap_document(sitemap_url)
                    )
                    if document_type != "urlset":
                        raise CrawlPageRejected(
                            "child sitemap is not a URL set"
                        )
                    self._sitemap_documents[final_url] = entries
                    return final_url, entries, None
                except Exception as error:
                    return sitemap_url, (), error

        outcomes = await asyncio.gather(*(load_one(url) for url in selected))
        discovered = {}
        failures = 0
        for sitemap_url, entries, error in outcomes:
            if error is not None:
                failures += 1
                LOGGER.warning(
                    "Could not load sitemap %s: %s", sitemap_url, error
                )
                continue
            for candidate, last_modified in entries:
                normalized = normalize_crawl_url(
                    candidate,
                    sitemap_url,
                    self.base_url,
                    config["allowed_path_prefix"],
                )
                if normalized:
                    discovered.setdefault(normalized, {
                        "sitemap_url": sitemap_url,
                        "sitemap_lastmod": last_modified,
                    })
        return discovered, len(selected), failures

    async def _discover_from_wordpress_search(self, config):
        discovery_path = config.get("discovery_path")
        subtype = config.get("discovery_subtype")
        if not discovery_path or not subtype or self.max_pages <= 1:
            return set()

        endpoint = urljoin(self.base_url + "/", discovery_path)
        endpoint_parts = urlsplit(endpoint)
        approved_parts = urlsplit(self.base_url)
        if (
            endpoint_parts.scheme.lower() not in {"http", "https"}
            or _host_key(endpoint_parts) != _host_key(approved_parts)
        ):
            raise CrawlPageRejected(
                "search discovery endpoint is outside the approved host"
            )

        urls = {}
        per_page = min(100, self.max_pages - 1)
        page = 1
        total_pages = 1
        while page <= total_pages and len(urls) < self.max_pages - 1:
            response = await self._request(
                endpoint,
                auth=self.auth,
                headers={**self.headers, "Accept": "application/json"},
                params={
                    "type": "post",
                    "subtype": subtype,
                    "per_page": per_page,
                    "page": page,
                },
                follow_redirects=False,
            )
            if _host_key(urlsplit(str(response.url))) != _host_key(
                approved_parts
            ):
                raise CrawlPageRejected(
                    "search discovery resolved outside the approved host"
                )
            media_type = response.headers.get(
                "content-type", ""
            ).split(";", 1)[0].lower()
            if media_type != "application/json":
                raise CrawlPageRejected(
                    "search discovery did not return JSON"
                )
            payload = response.json()
            if not isinstance(payload, list):
                raise CrawlPageRejected(
                    "search discovery returned an invalid payload"
                )

            try:
                total_pages = max(
                    1,
                    int(response.headers.get("X-WP-TotalPages", "1")),
                )
            except ValueError:
                total_pages = 1
            for item in payload:
                if not isinstance(item, dict):
                    continue
                normalized = normalize_crawl_url(
                    item.get("url"),
                    self.base_url + "/",
                    self.base_url,
                    config["allowed_path_prefix"],
                )
                if normalized:
                    urls.setdefault(normalized, {
                        "wordpress_id": item.get("id"),
                        "wordpress_subtype": item.get("subtype"),
                    })
            page += 1

        return urls

    async def _fetch_page(self, url, path_prefix, previous_record=None):
        conditional_headers = {}
        if previous_record:
            if previous_record.get("http_etag"):
                conditional_headers["If-None-Match"] = previous_record[
                    "http_etag"
                ]
            if previous_record.get("http_last_modified"):
                conditional_headers["If-Modified-Since"] = previous_record[
                    "http_last_modified"
                ]
        try:
            response, final_url = await self._get(
                url,
                path_prefix,
                conditional_headers=conditional_headers,
            )
        except Exception as error:
            return url, None, None, None, error
        if response.status_code == 304:
            return url, response, final_url, None, None
        try:
            # lxml parsing and the full-page rejection scan are synchronous CPU
            # work. Keep them off the event loop, and retain the parsed tree for
            # link discovery and extraction instead of parsing the body again.
            soup = await asyncio.to_thread(parse_and_validate_html, response.text)
            return url, response, final_url, soup, None
        except Exception as error:
            return url, response, final_url, None, error

    async def crawl_async(
        self,
        source_name,
        config,
        previous_records=None,
        incremental=False,
    ):
        # asyncio.run creates a new event loop for each source, so the lock must
        # be local to the current crawl. The monotonic deadline remains shared
        # to preserve the delay between consecutive source crawls.
        self._rate_lock = asyncio.Lock()
        await self._load_robots_policy()
        start_path = config.get("start_path")
        start_url = (
            normalize_crawl_url(
                start_path,
                self.base_url + "/",
                self.base_url,
                config["allowed_path_prefix"],
            )
            if start_path
            else None
        )
        if start_path and not start_url:
            raise CrawlPageRejected(
                "start path is outside the approved source"
            )
        queue = [start_url] if start_url else []
        queued = {start_url} if start_url else set()
        content_hashes = set()
        canonical_urls = set()
        discovery_metadata = {}
        previous_by_url = {}
        records = []
        summary = {
            "discovered": len(queued),
            "fetched": 0,
            "accepted": 0,
            "skipped": 0,
            "failed": 0,
            "limit_reached": False,
            "sitemaps_selected": 0,
            "sitemap_discovered": 0,
            "sitemap_failures": 0,
            "search_discovered": 0,
            "incremental": bool(incremental),
            "changed": 0,
            "reused": 0,
            "http_not_modified": 0,
        }

        if config.get("sitemap_include"):
            try:
                sitemap_records, selected_count, sitemap_failures = (
                    await self._discover_from_sitemap(config)
                )
                summary["sitemaps_selected"] = selected_count
                summary["sitemap_discovered"] = len(sitemap_records)
                summary["sitemap_failures"] = sitemap_failures
                summary["failed"] += sitemap_failures
                for discovered_url, metadata in sorted(
                    sitemap_records.items()
                ):
                    if len(queued) >= self.max_pages:
                        summary["limit_reached"] = True
                        break
                    discovery_metadata[discovered_url] = metadata
                    if discovered_url not in queued:
                        queue.append(discovered_url)
                        queued.add(discovered_url)
                        summary["discovered"] += 1
                LOGGER.info(
                    "Discovered %d %s URLs across %d sitemap files",
                    len(sitemap_records),
                    source_name,
                    selected_count,
                )
            except Exception as error:
                summary["sitemap_failures"] += 1
                summary["failed"] += 1
                LOGGER.warning(
                    "Could not use %s sitemap discovery: %s",
                    source_name,
                    error,
                )

        try:
            search_records = await self._discover_from_wordpress_search(config)
            search_urls = sorted(search_records)
            summary["search_discovered"] = len(search_urls)
            for discovered_url in search_urls:
                if len(queued) >= self.max_pages:
                    summary["limit_reached"] = True
                    break
                if discovered_url not in queued:
                    queue.append(discovered_url)
                    queued.add(discovered_url)
                    summary["discovered"] += 1
                discovery_metadata.setdefault(discovered_url, {}).update(
                    search_records[discovered_url]
                )
            if search_urls:
                LOGGER.info(
                    "Discovered %d %s article URLs through WordPress search",
                    len(search_urls),
                    source_name,
                )
        except Exception as error:
            LOGGER.warning(
                "Could not use %s search discovery: %s",
                source_name,
                error,
            )

        if incremental and previous_records:
            for previous in previous_records:
                if not isinstance(previous, dict):
                    continue
                previous_url = normalize_crawl_url(
                    previous.get("canonical_url") or previous.get("link"),
                    self.base_url + "/",
                    self.base_url,
                    config["allowed_path_prefix"],
                )
                if previous_url:
                    previous_by_url[previous_url] = previous

            remaining_queue = []
            for url in queue:
                metadata = discovery_metadata.get(url, {})
                sitemap_lastmod = metadata.get("sitemap_lastmod")
                previous = previous_by_url.get(url)
                if (
                    previous is None
                    or not sitemap_lastmod
                    or previous.get("sitemap_lastmod") != sitemap_lastmod
                ):
                    remaining_queue.append(url)
                    continue

                reused = dict(previous)
                reused["incremental_reused"] = True
                reused["sitemap_url"] = metadata.get("sitemap_url")
                reused["sitemap_lastmod"] = sitemap_lastmod
                reused["discovered_via"] = "sitemap"
                canonical_url = reused.get("canonical_url") or reused.get("link")
                content_hash = reused.get("content_hash")
                if canonical_url in canonical_urls or (
                    content_hash and content_hash in content_hashes
                ):
                    summary["skipped"] += 1
                    continue
                canonical_urls.add(canonical_url)
                if content_hash:
                    content_hashes.add(content_hash)
                records.append(reused)
                summary["accepted"] += 1
                summary["reused"] += 1
            queue = remaining_queue
            if summary["reused"]:
                LOGGER.info(
                    "Reused %d unchanged %s pages from sitemap last-modified "
                    "metadata",
                    summary["reused"],
                    source_name,
                )

        frontier = asyncio.Queue()
        for queued_url in queue:
            frontier.put_nowait(queued_url)
        candidate_records = []
        state_lock = asyncio.Lock()

        async def add_discovered_links(links):
            async with state_lock:
                for link in sorted(links):
                    if link in queued:
                        continue
                    if len(queued) >= self.max_pages:
                        summary["limit_reached"] = True
                        continue
                    queued.add(link)
                    summary["discovered"] += 1
                    frontier.put_nowait(link)

        async def process_url(url):
            previous = previous_by_url.get(url) if incremental else None
            url, response, final_url, soup, page_error = await self._fetch_page(
                url,
                config["allowed_path_prefix"],
                previous_record=previous,
            )
            if response is not None:
                summary["fetched"] += 1
            if page_error is not None:
                if isinstance(page_error, CrawlPageSkipped):
                    summary["skipped"] += 1
                    LOGGER.info("Skipping empty page %s: %s", url, page_error)
                    return
                if url == start_url and summary["discovered"] > 1:
                    summary["skipped"] += 1
                    LOGGER.warning(
                        "Skipping unavailable %s landing page; fallback "
                        "discovery succeeded: %s",
                        source_name,
                        page_error,
                    )
                else:
                    summary["failed"] += 1
                    LOGGER.warning(
                        "Could not ingest %s page %s: %s",
                        source_name,
                        url,
                        page_error,
                    )
                return

            if response.status_code == 304:
                if previous is None:
                    summary["failed"] += 1
                    LOGGER.warning(
                        "Received HTTP 304 for %s without a previous record",
                        url,
                    )
                    return
                reused = dict(previous)
                reused["incremental_reused"] = True
                reused["revalidated_at"] = datetime.now(
                    timezone.utc
                ).isoformat()
                summary["http_not_modified"] += 1
                metadata = discovery_metadata.get(url, {})
                if metadata.get("sitemap_url"):
                    reused["sitemap_url"] = metadata["sitemap_url"]
                    reused["sitemap_lastmod"] = metadata.get(
                        "sitemap_lastmod"
                    )
                    reused["discovered_via"] = "sitemap"
                async with state_lock:
                    candidate_records.append((reused, False))
                return

            if config.get("follow_page_links", True):
                links = await asyncio.to_thread(
                    discover_links,
                    soup,
                    final_url,
                    self.base_url,
                    config["allowed_path_prefix"],
                )
                await add_discovered_links(links)

            if (
                start_url
                and (url == start_url or final_url == start_url)
                and not config.get("ingest_start_page", False)
            ):
                summary["skipped"] += 1
                return
            try:
                record = await asyncio.to_thread(
                    extract_article,
                    soup,
                    final_url,
                    self.base_url,
                    source_name,
                    config,
                    None,
                    True,
                )
            except Exception as extraction_error:
                summary["failed"] += 1
                LOGGER.warning(
                    "Could not extract %s page %s: %s",
                    source_name,
                    final_url,
                    extraction_error,
                )
                return
            sitemap_metadata = discovery_metadata.get(url)
            if sitemap_metadata:
                if sitemap_metadata.get("sitemap_url"):
                    record["sitemap_url"] = sitemap_metadata["sitemap_url"]
                    record["sitemap_lastmod"] = sitemap_metadata.get(
                        "sitemap_lastmod"
                    )
                    record["discovered_via"] = "sitemap"
                    if not record.get("updated_at"):
                        record["updated_at"] = sitemap_metadata.get(
                            "sitemap_lastmod"
                        )
                if sitemap_metadata.get("wordpress_id"):
                    record["id"] = sitemap_metadata["wordpress_id"]
                    record["wordpress_id"] = sitemap_metadata["wordpress_id"]
                    record["wordpress_subtype"] = sitemap_metadata.get(
                        "wordpress_subtype"
                    )
            if response.headers.get("etag"):
                record["http_etag"] = response.headers["etag"]
            if response.headers.get("last-modified"):
                record["http_last_modified"] = response.headers[
                    "last-modified"
                ]
            async with state_lock:
                candidate_records.append((record, True))

        async def worker():
            while True:
                url = await frontier.get()
                try:
                    await process_url(url)
                except Exception as error:
                    summary["failed"] += 1
                    LOGGER.exception(
                        "Unexpected error processing %s page %s: %s",
                        source_name,
                        url,
                        error,
                    )
                finally:
                    frontier.task_done()

        if not frontier.empty():
            LOGGER.info(
                "Crawling %d queued %s pages with %d persistent workers",
                frontier.qsize(),
                source_name,
                self.concurrency,
            )
            workers = [
                asyncio.create_task(worker())
                for _ in range(self.concurrency)
            ]
            try:
                await frontier.join()
            finally:
                for task in workers:
                    task.cancel()
                await asyncio.gather(*workers, return_exceptions=True)

        # Worker completion order is intentionally nondeterministic. Sort before
        # deduplication so identical inputs still produce stable snapshots.
        for record, changed in sorted(
            candidate_records,
            key=lambda item: str(item[0].get("canonical_url", "")),
        ):
            if record["canonical_url"] in canonical_urls:
                summary["skipped"] += 1
                LOGGER.warning(
                    "Skipping duplicate canonical URL %s",
                    record["canonical_url"],
                )
                continue
            if record["content_hash"] in content_hashes:
                summary["skipped"] += 1
                LOGGER.warning(
                    "Skipping duplicate HTML content at %s",
                    record["canonical_url"],
                )
                continue
            canonical_urls.add(record["canonical_url"])
            content_hashes.add(record["content_hash"])
            records.append(record)
            summary["accepted"] += 1
            if changed:
                summary["changed"] += 1
            else:
                summary["reused"] += 1

        records.sort(
            key=lambda item: str(
                item.get("canonical_url") or item.get("link") or item.get("id")
            )
        )
        if summary["limit_reached"]:
            LOGGER.warning(
                "%s crawl reached HTML_CRAWL_MAX_PAGES=%d",
                source_name,
                self.max_pages,
            )
        if not records:
            LOGGER.warning(
                "%s crawl accepted no article documents", source_name
            )
        LOGGER.info(
            "%s crawl summary: discovered=%d fetched=%d accepted=%d "
            "skipped=%d failed=%d reused=%d http_not_modified=%d",
            source_name,
            summary["discovered"],
            summary["fetched"],
            summary["accepted"],
            summary["skipped"],
            summary["failed"],
            summary["reused"],
            summary["http_not_modified"],
        )
        return records, summary

    async def _crawl_with_owned_client(
        self,
        source_name,
        config,
        previous_records=None,
        incremental=False,
    ):
        async with AsyncRetryingHttpClient(
            timeout=self.timeout,
            max_retries=self.max_retries,
            backoff_seconds=self.backoff_seconds,
            max_connections=self.concurrency,
        ) as http_client:
            self.http_client = http_client
            try:
                return await self.crawl_async(
                    source_name,
                    config,
                    previous_records=previous_records,
                    incremental=incremental,
                )
            finally:
                self.http_client = None

    def crawl(
        self,
        source_name,
        config,
        previous_records=None,
        incremental=False,
    ):
        if self.http_client is not None:
            return asyncio.run(self.crawl_async(
                source_name,
                config,
                previous_records=previous_records,
                incremental=incremental,
            ))
        return asyncio.run(
            self._crawl_with_owned_client(
                source_name,
                config,
                previous_records=previous_records,
                incremental=incremental,
            )
        )
