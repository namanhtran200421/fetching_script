WORDPRESS_SOURCES = {
    "research": {
        "endpoint": "academic_paper",
        "content_type": "research",
        "access_class": "member_restricted",
        "fields": "id,title,content,modified,link"
    },

    "info_hub": {
        "endpoint": "epkb_post_type_1",
        "content_type": "info_hub",
        "access_class": "member_restricted",
        "fields": "id,title,content,modified,link"
    },

    "anthropedia": {
        "endpoint": "epkb_post_type_2",
        "content_type": "anthropedia",
        "access_class": "member_restricted",
        "fields": "id,title,content,modified,link"
    },

    "cign_events": {
        "endpoint": "cign-event",
        "content_type": "cign_event",
        "access_class": "member_restricted",
        "fields": "id,title,content,modified,link"
    },

    "pages": {
        "endpoint": "pages",
        "content_type": "page",
        "access_class": "member_restricted",
        "fields": "id,title,content,modified,link"
    },

    "mec_event_definitions": {
        "endpoint": "mec-events",
        "content_type": "calendar_event_definition",
        "access_class": "member_restricted",
        "fields": "id,title,content,modified,link,mec_category"
    }
}