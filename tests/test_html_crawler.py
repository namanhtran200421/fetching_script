import asyncio
import unittest
from unittest.mock import patch

import httpx

from app.ingestion import html_crawler as html_crawler_module
from app.ingestion.html_crawler import (
    CrawlPageRejected,
    CrawlPageSkipped,
    HtmlCrawler,
    discover_links,
    extract_article,
    normalize_crawl_url,
    parse_sitemap_xml,
)
from app.ingestion.source_registry import HTML_SOURCES, REST_SOURCES, SOURCES


BASE_URL = "https://membership.example.test"
CONFIG = {
    "start_path": "/info-hub/",
    "allowed_path_prefix": "/info-hub/",
    "article_selectors": (".entry-content.content",),
    "minimum_content_characters": 30,
}


def article_html(title="A useful article", canonical="/info-hub/useful/", body=None):
    body = body or (
        "<h2>Introduction</h2><p>This is useful cultural information with "
        "enough detail to be accepted by the crawler.</p>"
    )
    canonical_tag = (
        '<link rel="canonical" href="%s">' % canonical if canonical is not None else ""
    )
    return """<!doctype html><html><head><title>{title} | Site</title>{canonical}</head>
    <body><header>Repeated header</header><article><h1>{title}</h1>
    <div class="breadcrumbs">Home / Info Hub</div>
    <div class="entry-content content">{body}<nav>Previous article</nav>
    <form>Search me</form><div class="rating">Five stars</div>
    <script>bad()</script><footer>Repeated footer</footer></div></article></body></html>""".format(
        title=title, canonical=canonical_tag, body=body
    )


class FakeResponse:
    def __init__(self, url, html="", status_code=200, headers=None, payload=None):
        self.url = httpx.URL(url)
        self.text = html
        self.content = html.encode("utf-8")
        self.status_code = status_code
        self.headers = headers or {"content-type": "text/html; charset=UTF-8"}
        self.payload = payload

    def json(self):
        return self.payload


class FakeHttpClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    async def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        result = self.responses[url]
        if isinstance(result, Exception):
            raise result
        return result


class ConcurrentFakeHttpClient(FakeHttpClient):
    def __init__(self, responses):
        super().__init__(responses)
        self.active = 0
        self.max_active = 0

    async def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0.01)
            result = self.responses[url]
            if isinstance(result, Exception):
                raise result
            return result
        finally:
            self.active -= 1


class HtmlCrawlerTests(unittest.TestCase):
    def test_info_hub_discovery_resolves_and_normalizes_links(self):
        html = """
        <a href="child#section">Child</a>
        <a href="/info-hub/child/">Duplicate</a>
        <a href="https://membership.example.test/info-hub/absolute">Absolute</a>
        <a href="https://outside.example/info-hub/nope/">External</a>
        <a href="/info-hubris/nope/">Similar prefix</a>
        <a href="mailto:test@example.test">Email</a>
        """
        links = discover_links(
            html, BASE_URL + "/info-hub/", BASE_URL, "/info-hub/"
        )

        self.assertEqual(links, {
            BASE_URL + "/info-hub/child/",
            BASE_URL + "/info-hub/absolute/",
        })

    def test_anthropedia_discovery_has_exact_prefix(self):
        links = discover_links(
            '<a href="topic">Topic</a><a href="/anthropedia-plus/no">No</a>',
            BASE_URL + "/anthropedia/", BASE_URL, "/anthropedia/",
        )
        self.assertEqual(links, {BASE_URL + "/anthropedia/topic/"})

    def test_fragment_and_trailing_slash_have_one_identity(self):
        first = normalize_crawl_url(
            "/info-hub/item#top", BASE_URL, BASE_URL, "/info-hub/"
        )
        second = normalize_crawl_url(
            "/info-hub/item/", BASE_URL, BASE_URL, "/info-hub/"
        )
        self.assertEqual(first, second)

    def test_root_prefix_allows_any_same_host_path(self):
        self.assertEqual(
            normalize_crawl_url(
                "/ai_question/example/", BASE_URL, BASE_URL, "/"
            ),
            BASE_URL + "/ai_question/example/",
        )

    def test_sitemap_parser_reads_locations_and_last_modified_dates(self):
        document_type, entries = parse_sitemap_xml(b"""<?xml version="1.0"?>
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
          <url><loc>https://membership.example.test/a/</loc>
          <lastmod>2026-09-01T00:00:00+00:00</lastmod></url>
        </urlset>""")

        self.assertEqual(document_type, "urlset")
        self.assertEqual(entries, [(
            BASE_URL + "/a/", "2026-09-01T00:00:00+00:00"
        )])

    def test_sitemap_only_source_accepts_valid_xml_served_with_404(self):
        root_url = BASE_URL + "/wp-sitemap.xml"
        child_url = BASE_URL + "/wp-sitemap-posts-ai_profile-1.xml"
        article_url = BASE_URL + "/ai_bot_profile/useful/"
        root_xml = f"""<?xml version="1.0"?>
        <sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
          <sitemap><loc>{child_url}</loc></sitemap>
        </sitemapindex>"""
        child_xml = f"""<?xml version="1.0"?>
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
          <url><loc>{article_url}</loc><lastmod>2026-09-01</lastmod></url>
        </urlset>"""
        xml_headers = {"content-type": "application/xml; charset=UTF-8"}
        client = FakeHttpClient({
            root_url: FakeResponse(
                root_url, root_xml, status_code=404, headers=xml_headers
            ),
            child_url: FakeResponse(
                child_url, child_xml, status_code=404, headers=xml_headers
            ),
            article_url: FakeResponse(
                article_url,
                article_html(
                    "Sitemap profile",
                    "/ai_bot_profile/useful/",
                ),
            ),
        })
        config = {
            "allowed_path_prefix": "/",
            "sitemap_include": ("wp-sitemap-posts-ai_profile-*.xml",),
            "follow_page_links": False,
            "article_selectors": (".entry-content.content",),
            "minimum_content_characters": 30,
            "content_type": "website_page",
            "access_class": "member_restricted",
        }

        records, summary = HtmlCrawler(BASE_URL, client).crawl(
            "website_sitemap", config
        )

        self.assertEqual(len(records), 1)
        self.assertEqual(summary["sitemap_discovered"], 1)
        self.assertEqual(summary["sitemaps_selected"], 1)
        self.assertEqual(summary["failed"], 0)
        self.assertEqual(records[0]["updated_at"], "2026-09-01")
        self.assertEqual(records[0]["sitemap_url"], child_url)

    def test_extracts_canonical_title_body_dates_and_provenance(self):
        html = article_html(
            body=(
                '<time class="published" datetime="2026-01-02">2 January</time>'
                '<time class="updated">Updated 3 January 2026</time>'
                "<h2>Introduction</h2><p>Meaningful article content that is "
                "long enough for reliable retrieval and testing.</p>"
            )
        )
        record = extract_article(
            html, BASE_URL + "/info-hub/useful/", BASE_URL, "info_hub", CONFIG,
            retrieved_at="2026-01-04T00:00:00+00:00",
        )

        self.assertEqual(record["title"], "A useful article")
        self.assertEqual(record["canonical_url"], BASE_URL + "/info-hub/useful/")
        self.assertEqual(record["published_at"], "2026-01-02")
        self.assertEqual(record["updated_at"], "Updated 3 January 2026")
        self.assertEqual(record["acquisition_method"], "html")
        self.assertIn("Meaningful article content", record["content_html"])
        # Fetching preserves the selected source HTML. Removing this embedded
        # page noise belongs to the independently rerunnable cleaning stage.
        for noise in ("Previous article", "Search me", "Five stars", "bad()"):
            self.assertIn(noise, record["content_html"])
        self.assertNotIn("content_text", record)

    def test_extracts_verified_echo_kb_title_and_dates(self):
        html = """<html><head><title>Info hub</title>
        <link rel="canonical" href="/info-hub/data-is-important/"></head><body>
        <main><h1>Info hub</h1><article id="eckb-article-content">
        <div id="eckb-article-content-title-container">
          <h1 class="eckb-article-title">Data is Important</h1>
        </div>
        <div class="eckb-article-content-created-date-container">
          Posted <time class="entry-date">August 1, 2024</time>
        </div>
        <div class="eckb-article-content-last-updated-date-container">
          Updated <time class="entry-date">August 2, 2024</time>
        </div>
        <div id="eckb-article-content-body"><p>This is the complete article body
        with enough useful cultural information to pass validation.</p></div>
        </article></main></body></html>"""

        record = extract_article(
            html, BASE_URL + "/info-hub/data-is-important/", BASE_URL,
            "info_hub", CONFIG,
        )

        self.assertEqual(record["title"], "Data is Important")
        self.assertEqual(record["published_at"], "August 1, 2024")
        self.assertEqual(record["updated_at"], "August 2, 2024")
        self.assertNotIn("Info hub", record["content_html"])

    def test_missing_canonical_falls_back_and_missing_dates_stay_none(self):
        record = extract_article(
            article_html(canonical=None), BASE_URL + "/info-hub/fallback", BASE_URL,
            "info_hub", CONFIG,
        )
        self.assertEqual(record["canonical_url"], BASE_URL + "/info-hub/fallback/")
        self.assertIsNone(record["published_at"])
        self.assertIsNone(record["updated_at"])

    def test_rejects_access_denied_empty_and_external_canonical_pages(self):
        denied = article_html(body="<p>Membership Required. Please log in.</p>")
        empty = article_html(body="<div></div>")
        external = article_html(canonical="https://outside.example/info-hub/a/")

        for html in (denied, empty, external):
            with self.assertRaises(CrawlPageRejected):
                extract_article(
                    html, BASE_URL + "/info-hub/useful/", BASE_URL,
                    "info_hub", CONFIG,
                )

    def test_no_results_archive_is_an_intentional_skip(self):
        html = article_html(
            body=(
                "<h2>Nothing to Show Right Now</h2><p>It appears whatever you "
                "were looking for is no longer here.</p>"
            )
        )
        with self.assertRaises(CrawlPageSkipped):
            extract_article(
                html,
                BASE_URL + "/info-hub/empty/",
                BASE_URL,
                "info_hub",
                CONFIG,
            )

    def test_faq_start_page_is_ingested_with_questions_and_answers(self):
        faq_base_url = "https://atlas.example.test"
        faq_url = faq_base_url + "/faq/"
        faq_config = {
            "start_path": "/faq/",
            "allowed_path_prefix": "/faq/",
            "ingest_start_page": True,
            "follow_page_links": False,
            "article_selectors": (".entry-content.content",),
            "structured_text_selectors": (
                "h1", "[role='tab']", ".x-acc-header-text",
                ".x-acc-content",
            ),
            "minimum_content_characters": 30,
        }
        faq_html = """<html><head><link rel="canonical" href="/faq/"></head>
        <body><article><div class="entry-content content">
        <h1>Frequently Asked Questions</h1>
        <button role="tab">Survey participants</button>
        <button><span class="x-acc-header-text">What is Diversity?</span></button>
        <div class="x-acc-content">Everyone is different, and diversity covers
        the attributes that make people different from one another.</div>
        </div></article></body></html>"""
        crawler = HtmlCrawler(
            faq_base_url,
            FakeHttpClient({faq_url: FakeResponse(faq_url, faq_html)}),
        )

        records, summary = crawler.crawl("faq", faq_config)

        self.assertEqual(summary["accepted"], 1)
        self.assertEqual(records[0]["title"], "Frequently Asked Questions")
        self.assertIn("Survey participants", records[0]["content_html"])
        self.assertIn("What is Diversity?", records[0]["content_html"])
        self.assertIn("Everyone is different", records[0]["content_html"])
        self.assertNotIn("content_text", records[0])

    def test_access_denied_landing_page_is_a_reported_failure(self):
        start = BASE_URL + "/info-hub/"
        denied = '<html><body class="pmpro-no-access">Membership Required</body></html>'
        crawler = HtmlCrawler(
            BASE_URL, FakeHttpClient({start: FakeResponse(start, denied)})
        )

        with self.assertLogs("app.ingestion.html_crawler", level="WARNING"):
            records, summary = crawler.crawl("info_hub", CONFIG)

        self.assertEqual(records, [])
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(summary["skipped"], 0)

    def test_robots_crawl_delay_is_applied_to_html_requests(self):
        robots_url = BASE_URL + "/robots.txt"
        start = BASE_URL + "/info-hub/"
        delays = []

        async def record_delay(delay):
            delays.append(delay)

        crawler = HtmlCrawler(
            BASE_URL,
            FakeHttpClient({
                robots_url: FakeResponse(
                    robots_url,
                    "User-agent: *\nCrawl-delay: 30\n",
                    headers={"content-type": "text/plain"},
                ),
                start: FakeResponse(start, article_html()),
            }),
            respect_robots=True,
            sleep=record_delay,
        )

        crawler.crawl("info_hub", CONFIG)

        self.assertEqual(crawler._effective_request_interval, 30)
        self.assertEqual(len(delays), 1)
        self.assertGreater(delays[0], 29)

    def test_fallback_token_bucket_allows_burst_then_preserves_rate(self):
        urls = [BASE_URL + "/info-hub/%d/" % index for index in range(3)]
        clock_value = [0.0]
        delays = []

        async def advance_clock(delay):
            delays.append(delay)
            clock_value[0] += delay

        crawler = HtmlCrawler(
            BASE_URL,
            FakeHttpClient({url: FakeResponse(url) for url in urls}),
            minimum_request_interval=1,
            burst_size=2,
            sleep=advance_clock,
            clock=lambda: clock_value[0],
        )

        async def make_requests():
            await asyncio.gather(*(crawler._request(url) for url in urls))

        asyncio.run(make_requests())

        self.assertEqual(len(delays), 1)
        self.assertAlmostEqual(delays[0], 1.0)

    def test_robots_fetch_failure_disables_bursts(self):
        robots_url = BASE_URL + "/robots.txt"
        urls = [BASE_URL + "/info-hub/%d/" % index for index in range(2)]
        clock_value = [0.0]
        delays = []

        async def advance_clock(delay):
            delays.append(delay)
            clock_value[0] += delay

        client = FakeHttpClient({
            robots_url: RuntimeError("robots unavailable"),
            **{url: FakeResponse(url) for url in urls},
        })
        crawler = HtmlCrawler(
            BASE_URL,
            client,
            respect_robots=True,
            minimum_request_interval=1,
            burst_size=6,
            sleep=advance_clock,
            clock=lambda: clock_value[0],
        )

        async def make_requests():
            await crawler._load_robots_policy()
            await asyncio.gather(*(crawler._request(url) for url in urls))

        with self.assertLogs("app.ingestion.html_crawler", level="WARNING"):
            asyncio.run(make_requests())

        self.assertFalse(crawler._burst_allowed)
        self.assertEqual(delays, [1.0, 1.0])

    def test_each_fetched_page_is_parsed_once(self):
        start = BASE_URL + "/info-hub/"
        article_url = BASE_URL + "/info-hub/article/"
        client = FakeHttpClient({
            start: FakeResponse(start, '<a href="/info-hub/article/">A</a>'),
            article_url: FakeResponse(
                article_url,
                article_html(canonical="/info-hub/article/"),
            ),
        })
        crawler = HtmlCrawler(BASE_URL, client)

        with patch.object(
            html_crawler_module,
            "parse_html",
            wraps=html_crawler_module.parse_html,
        ) as parse:
            records, summary = crawler.crawl("info_hub", CONFIG)

        self.assertEqual(summary["fetched"], 2)
        self.assertEqual(len(records), 1)
        self.assertEqual(parse.call_count, 2)

    def test_persistent_workers_do_not_wait_for_slowest_page(self):
        start = BASE_URL + "/info-hub/"
        urls = [BASE_URL + "/info-hub/%s/" % name for name in ("a", "b", "c")]
        landing = "".join('<a href="%s">Item</a>' % url for url in urls)

        class LongTailClient(FakeHttpClient):
            def __init__(self, responses):
                super().__init__(responses)
                self.slow_release = None
                self.third_started_before_slow_finished = False

            async def get(self, url, **kwargs):
                self.calls.append((url, kwargs))
                if url == urls[0]:
                    self.slow_release = asyncio.Event()
                    await self.slow_release.wait()
                elif url == urls[2]:
                    self.third_started_before_slow_finished = not (
                        self.slow_release.is_set()
                    )
                    self.slow_release.set()
                return self.responses[url]

        responses = {start: FakeResponse(start, landing)}
        for index, url in enumerate(urls):
            responses[url] = FakeResponse(
                url,
                article_html(
                    "Item %d" % index,
                    "/info-hub/%s/" % ("a", "b", "c")[index],
                    "<p>Unique complete worker-pool article %d with enough "
                    "content for reliable extraction.</p>" % index,
                ),
            )
        client = LongTailClient(responses)
        crawler = HtmlCrawler(BASE_URL, client, concurrency=2)

        records, summary = crawler.crawl("info_hub", CONFIG)

        self.assertEqual(len(records), 3)
        self.assertEqual(summary["failed"], 0)
        self.assertTrue(client.third_started_before_slow_finished)

    def test_search_discovery_recovers_from_blocked_landing_page(self):
        config = {
            **CONFIG,
            "discovery_path": "/wp-json/wp/v2/search",
            "discovery_subtype": "epkb_post_type_1",
        }
        search_url = BASE_URL + "/wp-json/wp/v2/search"
        start = BASE_URL + "/info-hub/"
        article_url = BASE_URL + "/info-hub/data-is-important/"
        responses = {
            search_url: FakeResponse(
                search_url,
                headers={
                    "content-type": "application/json; charset=UTF-8",
                    "X-WP-TotalPages": "1",
                },
                payload=[{
                    "id": 1,
                    "subtype": "epkb_post_type_1",
                    "url": article_url,
                }],
            ),
            start: FakeResponse(
                start,
                '<html><body class="pmpro-no-access">Membership Required</body></html>',
            ),
            article_url: FakeResponse(
                article_url,
                article_html(canonical="/info-hub/data-is-important/"),
            ),
        }
        client = FakeHttpClient(responses)
        crawler = HtmlCrawler(BASE_URL, client)

        with self.assertLogs("app.ingestion.html_crawler", level="WARNING"):
            records, summary = crawler.crawl("info_hub", config)

        self.assertEqual(len(records), 1)
        self.assertEqual(summary["accepted"], 1)
        self.assertEqual(summary["skipped"], 1)
        self.assertEqual(summary["failed"], 0)
        self.assertTrue(any(call[0] == search_url for call in client.calls))

    def test_crawl_prevents_loops_deduplicates_urls_and_cleaned_content(self):
        landing = """
        <a href="/info-hub/a/">A</a><a href="/info-hub/a#again">A duplicate</a>
        <a href="/info-hub/b/">B</a>
        """
        repeated_body = (
            "<p>The exact same sufficiently long cleaned body is used for both "
            "pages to test content-level duplicate detection.</p>"
        )
        responses = {
            BASE_URL + "/info-hub/": FakeResponse(BASE_URL + "/info-hub/", landing),
            BASE_URL + "/info-hub/a/": FakeResponse(
                BASE_URL + "/info-hub/a/",
                article_html("A", "/info-hub/a/", repeated_body)
                + '<a href="/info-hub/">Loop</a>',
            ),
            BASE_URL + "/info-hub/b/": FakeResponse(
                BASE_URL + "/info-hub/b/",
                article_html("B", "/info-hub/b/", repeated_body),
            ),
        }
        crawler = HtmlCrawler(BASE_URL, FakeHttpClient(responses), max_pages=10)

        records, summary = crawler.crawl("info_hub", CONFIG)

        self.assertEqual(len(records), 1)
        self.assertEqual(summary, {
            "discovered": 3, "fetched": 3, "accepted": 1, "skipped": 2,
            "failed": 0, "limit_reached": False,
            "sitemaps_selected": 0, "sitemap_discovered": 0,
            "sitemap_failures": 0, "search_discovered": 0,
            "incremental": False, "changed": 1, "reused": 0,
            "http_not_modified": 0,
        })

    def test_incremental_crawl_reuses_unchanged_sitemap_document(self):
        sitemap_index = BASE_URL + "/wp-sitemap.xml"
        child_sitemap = BASE_URL + "/wp-sitemap-posts-epkb_post_type_1-1.xml"
        article_url = BASE_URL + "/info-hub/unchanged/"
        previous = extract_article(
            article_html(canonical="/info-hub/unchanged/"),
            article_url,
            BASE_URL,
            "info_hub",
            CONFIG,
        )
        previous["sitemap_lastmod"] = "2026-09-01T00:00:00+00:00"
        responses = {
            sitemap_index: FakeResponse(
                sitemap_index,
                """<?xml version="1.0"?><sitemapindex>
                <sitemap><loc>{}</loc></sitemap></sitemapindex>""".format(
                    child_sitemap
                ),
                headers={"content-type": "application/xml"},
            ),
            child_sitemap: FakeResponse(
                child_sitemap,
                """<?xml version="1.0"?><urlset><url><loc>{}</loc>
                <lastmod>2026-09-01T00:00:00+00:00</lastmod>
                </url></urlset>""".format(article_url),
                headers={"content-type": "application/xml"},
            ),
        }
        config = {
            **CONFIG,
            "start_path": None,
            "sitemap_include": (
                "wp-sitemap-posts-epkb_post_type_1-*.xml",
            ),
            "follow_page_links": False,
        }
        client = FakeHttpClient(responses)
        crawler = HtmlCrawler(BASE_URL, client)

        records, summary = crawler.crawl(
            "info_hub",
            config,
            previous_records=[previous],
            incremental=True,
        )

        self.assertEqual(len(records), 1)
        self.assertTrue(records[0]["incremental_reused"])
        self.assertEqual(summary["accepted"], 1)
        self.assertEqual(summary["reused"], 1)
        self.assertEqual(summary["changed"], 0)
        self.assertEqual(summary["http_not_modified"], 0)
        self.assertEqual(summary["fetched"], 0)
        self.assertFalse(any(call[0] == article_url for call in client.calls))

    def test_incremental_crawl_reuses_http_304_document(self):
        article_url = BASE_URL + "/info-hub/revalidated/"
        previous = extract_article(
            article_html(canonical="/info-hub/revalidated/"),
            article_url,
            BASE_URL,
            "info_hub",
            CONFIG,
        )
        previous["http_etag"] = '"version-1"'
        client = FakeHttpClient({
            article_url: FakeResponse(
                article_url,
                status_code=304,
                headers={"etag": '"version-1"'},
            ),
        })
        config = {
            **CONFIG,
            "start_path": article_url,
            "ingest_start_page": True,
            "follow_page_links": False,
        }
        crawler = HtmlCrawler(BASE_URL, client)

        records, summary = crawler.crawl(
            "info_hub",
            config,
            previous_records=[previous],
            incremental=True,
        )

        self.assertEqual(len(records), 1)
        self.assertTrue(records[0]["incremental_reused"])
        self.assertIn("revalidated_at", records[0])
        self.assertEqual(summary["reused"], 1)
        self.assertEqual(summary["changed"], 0)
        self.assertEqual(summary["http_not_modified"], 1)
        self.assertEqual(
            client.calls[0][1]["headers"]["If-None-Match"],
            '"version-1"',
        )

    def test_successful_fetch_persists_http_validators(self):
        article_url = BASE_URL + "/info-hub/validators/"
        response = FakeResponse(
            article_url,
            article_html(canonical="/info-hub/validators/"),
            headers={
                "content-type": "text/html",
                "etag": '"version-2"',
                "last-modified": "Wed, 09 Sep 2026 00:00:00 GMT",
            },
        )
        config = {
            **CONFIG,
            "start_path": article_url,
            "ingest_start_page": True,
            "follow_page_links": False,
        }

        records, summary = HtmlCrawler(
            BASE_URL,
            FakeHttpClient({article_url: response}),
        ).crawl("info_hub", config)

        self.assertEqual(summary["accepted"], 1)
        self.assertEqual(records[0]["http_etag"], '"version-2"')
        self.assertEqual(
            records[0]["http_last_modified"],
            "Wed, 09 Sep 2026 00:00:00 GMT",
        )

    def test_one_article_failure_does_not_abort_source(self):
        landing = '<a href="/info-hub/good/">Good</a><a href="/info-hub/bad/">Bad</a>'
        responses = {
            BASE_URL + "/info-hub/": FakeResponse(BASE_URL + "/info-hub/", landing),
            BASE_URL + "/info-hub/good/": FakeResponse(
                BASE_URL + "/info-hub/good/", article_html(canonical="/info-hub/good/")
            ),
            BASE_URL + "/info-hub/bad/": RuntimeError("connection reset"),
        }
        crawler = HtmlCrawler(BASE_URL, FakeHttpClient(responses))

        with self.assertLogs("app.ingestion.html_crawler", level="WARNING"):
            records, summary = crawler.crawl("info_hub", CONFIG)

        self.assertEqual(len(records), 1)
        self.assertEqual(summary["failed"], 1)

    def test_fetch_concurrency_is_bounded_and_parallel(self):
        start = BASE_URL + "/info-hub/"
        article_urls = [BASE_URL + "/info-hub/item-%d/" % i for i in range(5)]
        landing = "".join('<a href="%s">Item</a>' % url for url in article_urls)
        responses = {start: FakeResponse(start, landing)}
        for index, url in enumerate(article_urls):
            responses[url] = FakeResponse(
                url,
                article_html(
                    "Item %d" % index,
                    "/info-hub/item-%d/" % index,
                    "<p>Unique complete article body number %d with enough "
                    "content for reliable validation and extraction.</p>" % index,
                ),
            )
        client = ConcurrentFakeHttpClient(responses)
        crawler = HtmlCrawler(
            BASE_URL, client, max_pages=10, concurrency=2
        )

        records, summary = crawler.crawl("info_hub", CONFIG)

        self.assertEqual(len(records), 5)
        self.assertEqual(summary["failed"], 0)
        self.assertEqual(client.max_active, 2)

    def test_oversized_html_response_is_rejected(self):
        start = BASE_URL + "/info-hub/"
        response = FakeResponse(start, "<html>too large</html>")
        response.content = b"x" * 101
        crawler = HtmlCrawler(
            BASE_URL,
            FakeHttpClient({start: response}),
            max_response_bytes=100,
        )

        with self.assertLogs("app.ingestion.html_crawler", level="WARNING"):
            records, summary = crawler.crawl("info_hub", CONFIG)

        self.assertEqual(records, [])
        self.assertEqual(summary["failed"], 1)

    def test_external_redirect_is_rejected_before_following(self):
        start = BASE_URL + "/info-hub/"
        client = FakeHttpClient({
            start: FakeResponse(
                start, status_code=302,
                headers={"location": "https://outside.example/info-hub/"},
            )
        })
        crawler = HtmlCrawler(BASE_URL, client)

        with self.assertLogs("app.ingestion.html_crawler", level="WARNING"):
            records, summary = crawler.crawl("info_hub", CONFIG)

        self.assertEqual(records, [])
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(len(client.calls), 1)

    def test_registry_never_configures_prohibited_rest_endpoints(self):
        prohibited = {"epkb_post_type_1", "epkb_post_type_2", "echo_kb_faq"}
        self.assertTrue(prohibited.isdisjoint({c["endpoint"] for c in REST_SOURCES.values()}))
        self.assertEqual(
            set(HTML_SOURCES),
            {"info_hub", "anthropedia", "faq", "website_sitemap"},
        )
        self.assertNotIn("echo_kb_faq", SOURCES)
        self.assertEqual(
            SOURCES["faq"]["base_url"],
            "https://atlas.culturalinfusion.com",
        )
        self.assertEqual(SOURCES["faq"]["access_class"], "public")


if __name__ == "__main__":
    unittest.main()
