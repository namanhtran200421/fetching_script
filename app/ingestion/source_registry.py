from typing import Any, Dict

from app.config import ATLAS_PUBLIC_BASE_URL

# Every source on this origin is public and unauthenticated. Sharing one base
# URL keeps them on a single crawler and a single WordPress client per run, so
# they also share one robots policy, one request-rate budget, and one cached
# sitemap index. Each must therefore agree on sitemap_path.
ATLAS_PUBLIC_SITEMAP_PATH = "/sitemap_index.xml"

SOURCES: Dict[str, Dict[str, Any]] = {
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
        "base_url": ATLAS_PUBLIC_BASE_URL,
        "start_path": "/faq/",
        "allowed_path_prefix": "/faq/",
        "sitemap_path": ATLAS_PUBLIC_SITEMAP_PATH,
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
    },

    # ------------------------------------------------------------------
    # Public Atlas website. Everything below is served without credentials
    # and is safe to quote to non-members, so it carries access_class
    # "public". The public site's own mec-events are deliberately absent:
    # they are teaser stubs that end in a register-to-read-more link, and
    # the full event text is already ingested as mec_event_definitions.
    # ------------------------------------------------------------------

    "atlas_posts": {
        "acquisition_method": "rest",
        "base_url": ATLAS_PUBLIC_BASE_URL,
        "endpoint": "posts",
        "html_fallback": True,
        # Posts live at the site root, so the sitemap and search subtype,
        # not the path prefix, are what scope this source.
        "allowed_path_prefix": "/",
        "sitemap_path": ATLAS_PUBLIC_SITEMAP_PATH,
        "sitemap_include": ("post-sitemap.xml",),
        "discovery_path": "/wp-json/wp/v2/search",
        "discovery_subtype": "post",
        "follow_page_links": False,
        "allow_body_fallback": True,
        "article_selectors": (
            ".entry-content.content", ".entry-content", "#main-content",
        ),
        "minimum_content_characters": 30,
        "content_type": "public_article",
        "access_class": "public",
        "supports_modified_after": True,
        "taxonomy_fields": {
            "categories": "category",
            "tags": "post_tag",
        },
        "fields": (
            "id,title,content,excerpt,modified,modified_gmt,date_gmt,link,"
            "categories,tags"
        ),
    },

    "atlas_pages": {
        "acquisition_method": "rest",
        "base_url": ATLAS_PUBLIC_BASE_URL,
        "endpoint": "pages",
        "html_fallback": True,
        "allowed_path_prefix": "/",
        "sitemap_path": ATLAS_PUBLIC_SITEMAP_PATH,
        "sitemap_include": ("page-sitemap.xml",),
        "discovery_path": "/wp-json/wp/v2/search",
        "discovery_subtype": "page",
        "follow_page_links": False,
        "allow_body_fallback": True,
        "article_selectors": (
            ".entry-content.content", ".entry-content", "#main-content",
        ),
        "minimum_content_characters": 30,
        "content_type": "public_page",
        "access_class": "public",
        "supports_modified_after": True,
        # Placeholders, sign-in and error pages, empty quiz shells, and the
        # FAQ page that the dedicated faq source already ingests in full.
        "denylist_setting": "ATLAS_PAGE_DENYLIST",
        "fields": (
            "id,title,content,modified,modified_gmt,date_gmt,link"
        ),
    },

    "atlas_case_studies": {
        "acquisition_method": "rest",
        "base_url": ATLAS_PUBLIC_BASE_URL,
        "endpoint": "case-study",
        "html_fallback": True,
        "allowed_path_prefix": "/case-study/",
        # Case studies are absent from the sitemap, so the HTML fallback
        # relies on WordPress search discovery alone.
        "sitemap_path": ATLAS_PUBLIC_SITEMAP_PATH,
        "discovery_path": "/wp-json/wp/v2/search",
        "discovery_subtype": "case-study",
        "follow_page_links": False,
        "allow_body_fallback": True,
        "article_selectors": (
            ".entry-content.content", ".entry-content", "#main-content",
        ),
        "minimum_content_characters": 30,
        "content_type": "public_case_study",
        "access_class": "public",
        "supports_modified_after": True,
        "fields": (
            "id,title,content,modified,modified_gmt,date_gmt,link"
        ),
    },

    "atlas_media_documents": {
        "acquisition_method": "media_files",
        "base_url": ATLAS_PUBLIC_BASE_URL,
        "endpoint": "media",
        "content_type": "public_document",
        "access_class": "public",
        "supports_modified_after": True,
        "fields": (
            "id,title,link,source_url,mime_type,date_gmt,modified,"
            "modified_gmt,caption,alt_text,media_details"
        ),
    },

    # Reference data, not documents: term dictionaries used to resolve the
    # numeric taxonomy IDs on public posts into human-readable names.
    "atlas_taxonomies": {
        "acquisition_method": "taxonomy",
        "base_url": ATLAS_PUBLIC_BASE_URL,
        "role": "reference",
        "taxonomies": {
            "category": "categories",
            "post_tag": "tags",
            "mec_category": "mec_category",
        },
        "content_type": "public_taxonomy_term",
        "access_class": "public",
        "fields": "id,name,slug,description,count,taxonomy,parent,link",
    },
}


def is_reference_source(config):
    """Reference sources are fetched and stored, but never become documents."""
    return config.get("role") == "reference"


DOCUMENT_SOURCES = {
    name: config
    for name, config in SOURCES.items()
    if not is_reference_source(config)
}
REFERENCE_SOURCES = {
    name: config
    for name, config in SOURCES.items()
    if is_reference_source(config)
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
