SOURCES = {
    "research": {
        "acquisition_method": "rest",
        "endpoint": "academic_paper",
        "html_fallback": True,
        "allowed_path_prefix": "/academic-papers/",
        "sitemap_include": (
            "wp-sitemap-posts-academic_paper-*.xml",
        ),
        "discovery_path": "/wp-json/wp/v2/search",
        "discovery_subtype": "academic_paper",
        "follow_page_links": False,
        "allow_body_fallback": True,
        "article_selectors": (
            ".entry-content.content", ".entry-content", "#main-content",
        ),
        "minimum_content_characters": 30,
        "content_type": "research",
        "access_class": "member_restricted",
        "supports_modified_after": True,
        "fields": "id,title,content,modified,modified_gmt,link"
    },

    "info_hub": {
        "acquisition_method": "html",
        "start_path": "/info-hub/",
        "allowed_path_prefix": "/info-hub/",
        "sitemap_include": (
            "wp-sitemap-posts-epkb_post_type_1-*.xml",
        ),
        "discovery_path": "/wp-json/wp/v2/search",
        "discovery_subtype": "epkb_post_type_1",
        "article_selectors": (
            "#eckb-article-content-body",
            ".eckb-article-body",
            ".epkb-article-content",
            ".entry-content.content",
        ),
        "minimum_content_characters": 80,
        "content_type": "info_hub",
        "access_class": "member_restricted",
    },

    "anthropedia": {
        "acquisition_method": "html",
        "start_path": "/anthropedia/",
        "allowed_path_prefix": "/anthropedia/",
        "sitemap_include": (
            "wp-sitemap-posts-epkb_post_type_2-*.xml",
        ),
        "discovery_path": "/wp-json/wp/v2/search",
        "discovery_subtype": "epkb_post_type_2",
        "article_selectors": (
            "#eckb-article-content-body",
            ".eckb-article-body",
            ".epkb-article-content",
            ".entry-content.content",
        ),
        "minimum_content_characters": 80,
        "content_type": "anthropedia",
        "access_class": "member_restricted",
    },

    "cign_events": {
        "acquisition_method": "rest",
        "endpoint": "cign-event",
        "html_fallback": True,
        "allowed_path_prefix": "/cign-event/",
        "sitemap_include": (
            "wp-sitemap-posts-cign-event-*.xml",
        ),
        "discovery_path": "/wp-json/wp/v2/search",
        "discovery_subtype": "cign-event",
        "follow_page_links": False,
        "allow_body_fallback": True,
        "article_selectors": (
            ".entry-content.content", ".entry-content", "#main-content",
        ),
        "minimum_content_characters": 30,
        "content_type": "cign_event",
        "access_class": "member_restricted",
        "supports_modified_after": True,
        "fields": "id,title,content,modified,modified_gmt,link"
    },

    "pages": {
        "acquisition_method": "rest",
        "endpoint": "pages",
        "allowlist_setting": "WORDPRESS_PAGE_ALLOWLIST",
        "html_fallback": True,
        "allowed_path_prefix": "/",
        "sitemap_include": ("wp-sitemap-posts-page-*.xml",),
        "discovery_path": "/wp-json/wp/v2/search",
        "discovery_subtype": "page",
        "follow_page_links": False,
        "allow_body_fallback": True,
        "article_selectors": (
            ".entry-content.content", ".entry-content", "#main-content",
        ),
        "minimum_content_characters": 30,
        "content_type": "page",
        "access_class": "member_restricted",
        "supports_modified_after": True,
        "fields": "id,title,content,modified,modified_gmt,link"
    },

    # The membership dashboard links its help centre to this public Atlas page.
    # It is a different origin, so the ingestion job gives it a dedicated
    # unauthenticated crawler and never forwards WordPress credentials.
    "faq": {
        "acquisition_method": "html",
        "base_url": "https://atlas.culturalinfusion.com",
        "start_path": "/faq/",
        "allowed_path_prefix": "/faq/",
        "sitemap_path": "/sitemap_index.xml",
        "sitemap_include": ("page-sitemap.xml",),
        "ingest_start_page": True,
        "follow_page_links": False,
        "article_selectors": (".entry-content.content",),
        # Cornerstone uses buttons for tabs/questions and sometimes puts short
        # answers directly in divs rather than paragraphs. Select each semantic
        # FAQ unit so those answers are not lost or duplicated.
        "structured_text_selectors": (
            "h1", "h2", "h3", "h4", "h5", "h6",
            "[role='tab']",
            ".x-acc-header-text",
            ".x-acc-content",
        ),
        "minimum_content_characters": 500,
        "content_type": "faq",
        "access_class": "public",
    },

    # Catch every sitemap-only post type and taxonomy. Known post types above
    # remain REST-first (or use their tailored KB extractor), while any new
    # sitemap added by WordPress is picked up here automatically.
    "website_sitemap": {
        "acquisition_method": "html",
        "allowed_path_prefix": "/",
        "sitemap_include": ("wp-sitemap-*.xml",),
        "sitemap_exclude": (
            "wp-sitemap-posts-page-*.xml",
            "wp-sitemap-posts-cign-event-*.xml",
            "wp-sitemap-posts-epkb_post_type_1-*.xml",
            "wp-sitemap-posts-epkb_post_type_2-*.xml",
            "wp-sitemap-posts-mec-events-*.xml",
            "wp-sitemap-posts-academic_paper-*.xml",
        ),
        "follow_page_links": False,
        "allow_body_fallback": True,
        "article_selectors": (
            ".entry-content.content",
            ".entry-content",
            ".post-content",
            ".portfolio-content",
            "#main-content",
        ),
        "minimum_content_characters": 30,
        "content_type": "website_page",
        "access_class": "member_restricted",
    },

    "mec_event_definitions": {
        "acquisition_method": "mec_api",
        "discovery_path": "/wp-json/wp/v2/search",
        "discovery_subtype": "mec-events",
        "html_fallback": True,
        "allowed_path_prefix": "/events/",
        "sitemap_include": ("wp-sitemap-posts-mec-events-*.xml",),
        "follow_page_links": False,
        "allow_body_fallback": True,
        "article_selectors": (
            ".entry-content.content", ".entry-content", "#main-content",
        ),
        "minimum_content_characters": 30,
        "content_type": "calendar_event_definition",
        "access_class": "member_restricted",
        "fields": (
            "id,title,content,modified,modified_gmt,link,mec_category"
        )
    }
}

# Keep transport selection declarative and keep the old public name available to
# callers which use it as the complete set of content sources.
REST_SOURCES = {
    name: config
    for name, config in SOURCES.items()
    if config["acquisition_method"] == "rest"
}
HTML_SOURCES = {
    name: config
    for name, config in SOURCES.items()
    if config["acquisition_method"] == "html"
}
WORDPRESS_SOURCES = SOURCES

# These sources are expected to contain production data. A zero-record response
# is treated as a degraded run even when the endpoint itself returned HTTP 200.
SOURCE_MINIMUM_COUNTS = {
    **{name: 1 for name in SOURCES},
    "membership_plans": 1,
    "calendar_occurrences": 1,
}
