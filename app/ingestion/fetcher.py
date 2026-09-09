import logging

from app.ingestion.source_registry import SOURCES


LOGGER = logging.getLogger(__name__)


class IngestionFetcher:

    def __init__(
        self, wordpress_client, mec_client, html_crawler=None,
        html_crawlers=None, wordpress_page_allowlist=(),
        max_calendar_events=None,
    ):
        self.wordpress_client = wordpress_client
        self.mec_client = mec_client
        self.html_crawler = html_crawler
        self.html_crawlers = dict(html_crawlers or {})
        self.wordpress_page_allowlist = wordpress_page_allowlist
        self.max_calendar_events = max_calendar_events
        self.source_summaries = {}

    def _html_crawler_for(self, source_name):
        return self.html_crawlers.get(source_name, self.html_crawler)

    def _fetch_html_fallback(
        self,
        source_name,
        source,
        api_error,
        previous_records=None,
        incremental=False,
    ):
        html_crawler = self._html_crawler_for(source_name)
        if html_crawler is None or not source.get("html_fallback"):
            raise api_error
        LOGGER.warning(
            "%s API failed; falling back to sitemap HTML: %s",
            source_name,
            api_error,
        )
        records, summary = html_crawler.crawl(
            source_name,
            source,
            previous_records=previous_records,
            incremental=incremental,
        )
        summary["acquisition_method"] = "html"
        summary["api_fallback"] = True
        summary["api_error"] = {
            "type": type(api_error).__name__,
            "message": str(api_error),
        }
        if (
            source.get("allowlist_setting") == "WORDPRESS_PAGE_ALLOWLIST"
            and self.wordpress_page_allowlist
        ):
            approved = set(self.wordpress_page_allowlist)
            original_count = len(records)
            records = [record for record in records if record.get("id") in approved]
            summary["accepted"] = len(records)
            summary["skipped"] += original_count - len(records)
        self.source_summaries[source_name] = summary
        return records

    @staticmethod
    def _record_identity(record):
        if not isinstance(record, dict):
            return None
        return record.get("id") or record.get("link") or record.get(
            "canonical_url"
        )

    @classmethod
    def _merge_incremental_records(cls, previous_records, changed_records):
        merged = {}
        unkeyed = []
        for record in [*(previous_records or []), *(changed_records or [])]:
            identity = cls._record_identity(record)
            if identity is None:
                unkeyed.append(record)
            else:
                merged[identity] = record
        return [*merged.values(), *unkeyed]

    @staticmethod
    def _normalize_mec_detail(item):
        if not isinstance(item, dict):
            raise TypeError("MEC event detail item must be an object")
        payload = item.get("response")
        search_result = item.get("search_result") or {}
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
            raise ValueError("MEC event detail is missing its data object")
        data = payload["data"]
        post = data.get("post") if isinstance(data.get("post"), dict) else {}
        source_id = data.get("ID") or post.get("ID") or search_result.get("id")
        if isinstance(source_id, str) and source_id.isdigit():
            source_id = int(source_id)
        if not source_id:
            raise ValueError("MEC event detail is missing an id")
        return {
            "id": source_id,
            "title": data.get("title") or post.get("post_title")
            or search_result.get("title"),
            "content": data.get("content") or post.get("post_content") or "",
            "modified": post.get("post_modified"),
            "modified_gmt": post.get("post_modified_gmt"),
            "link": data.get("permalink") or search_result.get("url"),
            "mec_category": data.get("categories", {}),
            "source_type": "mec_event_definitions",
            "acquisition_method": "mec_api",
            # Preserve the complete API object in the immutable raw layer.
            "mec_response": payload,
        }

    def _fetch_mec_events(self, source_name, source):
        try:
            inventory = self.wordpress_client.search_all(
                source["discovery_subtype"]
            )
            selected_inventory = (
                inventory
                if self.max_calendar_events is None
                else inventory[:self.max_calendar_events]
            )
            detail_result = self.mec_client.get_event_details(
                selected_inventory
            )
        except Exception as error:
            return self._fetch_html_fallback(source_name, source, error)

        if not isinstance(detail_result, dict):
            return self._fetch_html_fallback(
                source_name,
                source,
                ValueError("MEC detail result must be an object"),
            )

        records = []
        failure_items = detail_result.get("failures", [])
        failures = list(failure_items) if isinstance(failure_items, list) else [{
            "event_id": None,
            "error": "MEC detail failures field was not a list",
        }]
        event_items = detail_result.get("events", [])
        if not isinstance(event_items, list):
            return self._fetch_html_fallback(
                source_name,
                source,
                ValueError("MEC detail events must be a list"),
            )
        for item in event_items:
            try:
                records.append(self._normalize_mec_detail(item))
            except (TypeError, ValueError) as error:
                failures.append({
                    "event_id": (item.get("search_result") or {}).get("id"),
                    "error": f"{type(error).__name__}: {error}",
                })
        if not records and selected_inventory:
            return self._fetch_html_fallback(
                source_name,
                source,
                RuntimeError(
                    "MEC detail API returned no usable events "
                    f"({len(failures)} failures)"
                ),
            )
        self.source_summaries[source_name] = {
            "discovered": len(inventory),
            "fetched": len(records) + len(failures),
            "accepted": len(records),
            "skipped": 0,
            "failed": len(failures),
            "limit_reached": len(selected_inventory) < len(inventory),
            "acquisition_method": "mec_api",
            "failure_details": failures[:100],
            "failure_details_truncated": len(failures) > 100,
        }
        return records

    def fetch_source(
        self,
        source_name: str,
        previous_records=None,
        modified_after=None,
        incremental=False,
    ):
        source = SOURCES.get(source_name)
        if not source:
            raise ValueError(f"Unknown source: {source_name}")

        if source["acquisition_method"] == "rest":
            include = (
                self.wordpress_page_allowlist or None
                if source.get("allowlist_setting") == "WORDPRESS_PAGE_ALLOWLIST"
                else None
            )
            use_delta = bool(
                incremental
                and previous_records is not None
                and modified_after
                and source.get("supports_modified_after")
            )
            delta_fallback_error = None
            try:
                if use_delta:
                    try:
                        reusable_records = previous_records
                        if include:
                            approved = set(include)
                            reusable_records = [
                                record for record in previous_records
                                if record.get("id") in approved
                            ]
                        changed_records = self.wordpress_client.get_all(
                            endpoint=source["endpoint"],
                            fields=source["fields"],
                            include=include,
                            modified_after=modified_after,
                        )
                        records = self._merge_incremental_records(
                            reusable_records,
                            changed_records,
                        )
                    except Exception as error:
                        delta_fallback_error = error
                        LOGGER.warning(
                            "%s incremental REST query failed; retrying a full "
                            "collection fetch: %s",
                            source_name,
                            error,
                        )
                        records = self.wordpress_client.get_all(
                            endpoint=source["endpoint"],
                            fields=source["fields"],
                            include=include,
                        )
                        changed_records = records
                        use_delta = False
                else:
                    records = self.wordpress_client.get_all(
                        endpoint=source["endpoint"],
                        fields=source["fields"],
                        include=include,
                    )
                    changed_records = records
            except Exception as error:
                return self._fetch_html_fallback(
                    source_name,
                    source,
                    error,
                    previous_records=previous_records,
                    incremental=incremental,
                )

            summary = {
                "discovered": len(records),
                "fetched": len(changed_records),
                "accepted": len(records),
                "skipped": 0,
                "failed": 0,
                "limit_reached": False,
                "acquisition_method": "rest",
                "incremental": use_delta,
                "changed": len(changed_records),
                "reused": max(0, len(records) - len(changed_records))
                if use_delta else 0,
            }
            if delta_fallback_error is not None:
                summary["incremental_fallback"] = {
                    "type": type(delta_fallback_error).__name__,
                    "message": str(delta_fallback_error),
                }
            self.source_summaries[source_name] = summary
            return records
        if source["acquisition_method"] == "html":
            html_crawler = self._html_crawler_for(source_name)
            if html_crawler is None:
                raise RuntimeError("HTML crawler is not configured")
            records, summary = html_crawler.crawl(
                source_name,
                source,
                previous_records=previous_records,
                incremental=incremental,
            )
            summary["acquisition_method"] = "html"
            self.source_summaries[source_name] = summary
            return records
        if source["acquisition_method"] == "mec_api":
            return self._fetch_mec_events(source_name, source)
        raise ValueError(
            f"Unsupported acquisition method for {source_name}: "
            f"{source['acquisition_method']}"
        )

    def fetch_all_sources(self):
        return {
            name: self.fetch_source(name)
            for name in SOURCES
        }

    def fetch_calendar_occurrences(
        self,
        event_definitions: list,
        max_events: int = None
    ):
        results = []
        failures = []
        definitions = (
            event_definitions
            if max_events is None
            else event_definitions[:max_events]
        )

        for index, event in enumerate(definitions, start=1):
            event_id = event.get("id")

            if not event_id:
                failures.append({
                    "event_id": None,
                    "error": "Event definition is missing an id",
                })
                continue

            LOGGER.info(
                "Resolving MEC event %d/%d: %s",
                index,
                len(definitions),
                event_id,
            )

            try:
                detail_payload = event.get("mec_response")
                if isinstance(detail_payload, dict):
                    occurrences = detail_payload.get("dates") or []
                    if not occurrences and detail_payload.get("date"):
                        occurrences = [detail_payload["date"]]
                    results.append({
                        "event_id": event_id,
                        "response": {
                            "success": True,
                            "occurrences": occurrences,
                            "derived_from": "mec_event_details",
                        },
                    })
                    continue
                response = self.mec_client.get_next_occurrences(event_id)
                results.append({
                    "event_id": event_id,
                    "response": response.json()
                })
            except Exception as error:
                # One broken event should not stop the full run.
                failures.append({
                    "event_id": event_id,
                    "error": str(error)
                })

        return {
            "events": results,
            "failures": failures
        }
