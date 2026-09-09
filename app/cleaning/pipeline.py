import logging
from datetime import datetime, timezone

from app.cleaning.normalizers import (
    SCHEMA_VERSION,
    normalize_calendar_event,
    normalize_membership_plan,
    normalize_wordpress_record,
)
from app.cleaning.validators import validate_document
from app.ingestion.source_registry import SOURCES


LOGGER = logging.getLogger(__name__)
CLEANING_VERSION = "2.1"


class CleaningPipeline:
    def __init__(self, storage):
        self.storage = storage

    def clean_run(self, run_id: str):
        started_at = datetime.now(timezone.utc)
        LOGGER.info("Starting cleaning run %s", run_id)
        raw_manifest = self.storage.load_manifest(run_id)
        raw_status = raw_manifest.get("status")
        if (
            raw_status is None
            and raw_manifest.get("run_id") == run_id
            and isinstance(raw_manifest.get("source_counts"), dict)
        ):
            raw_status = "legacy_complete"

        if raw_status not in {
            "complete",
            "complete_with_errors",
            "legacy_complete",
        }:
            raise ValueError(
                f"Cannot clean raw run {run_id}: "
                f"status is {raw_manifest.get('status')!r}"
            )

        source_counts: dict = {}
        quarantine_counts: dict = {}
        cleaned_outputs: dict = {}
        unavailable_sources = []
        manifest_source_counts = raw_manifest.get("source_counts")
        manifest_has_inventory = (
            isinstance(manifest_source_counts, dict)
            and bool(manifest_source_counts)
        )

        try:
            for source_name, source_config in SOURCES.items():
                if (
                    manifest_has_inventory
                    and source_name not in manifest_source_counts
                ):
                    # A source added after this immutable raw run cannot exist
                    # in that run. Keep historical runs re-cleanable.
                    unavailable_sources.append(source_name)
                    LOGGER.info(
                        "Skipping %s because it is unavailable in this raw run",
                        source_name,
                    )
                    continue
                LOGGER.info("Cleaning source %s", source_name)
                raw_records = self.storage.load_source(run_id, source_name)
                if not isinstance(raw_records, list):
                    raise ValueError(f"Raw source {source_name} must be a list")

                documents, normalization_failures = self._normalize_records(
                    enumerate(raw_records),
                    lambda _, record: normalize_wordpress_record(
                        record, source_name, source_config, run_id
                    ),
                )
                self._write_source(
                    run_id,
                    source_name,
                    documents,
                    normalization_failures,
                    source_counts,
                    quarantine_counts,
                    cleaned_outputs,
                )

            self._clean_memberships(
                run_id,
                source_counts,
                quarantine_counts,
                cleaned_outputs,
            )

            self._clean_calendar(
                run_id,
                source_counts,
                quarantine_counts,
                cleaned_outputs,
            )

            total_quarantined = sum(quarantine_counts.values())
            completed_at = datetime.now(timezone.utc)
            status = (
                "complete_with_errors"
                if total_quarantined or raw_status == "complete_with_errors"
                else "complete"
            )
            manifest = {
                "run_id": run_id,
                "status": status,
                "cleaning_version": CLEANING_VERSION,
                "document_schema_version": SCHEMA_VERSION,
                "canonical_content_field": "content_markdown",
                "content_format": "text/markdown",
                "started_at": started_at.isoformat(),
                "completed_at": completed_at.isoformat(),
                "duration_seconds": round(
                    (completed_at - started_at).total_seconds(), 3
                ),
                "raw_manifest_status": raw_status,
                "source_counts": source_counts,
                "quarantine_counts": quarantine_counts,
                "total_documents": sum(source_counts.values()),
                "total_quarantined": total_quarantined,
                "output_format": "application/x-ndjson",
                "cleaned_outputs": cleaned_outputs,
                "unavailable_sources": unavailable_sources,
                "quality_gate_passed": status == "complete",
                "published_latest": True,
            }
            manifest_path = self.storage.save_cleaned_manifest(
                run_id, manifest
            )
            self.storage.publish_cleaned_latest(
                run_id, manifest_path, status=status
            )
            LOGGER.info(
                "Cleaned run %s into %d full documents (%s)",
                run_id,
                manifest["total_documents"],
                status,
            )
            return manifest
        except Exception as error:
            completed_at = datetime.now(timezone.utc)
            failure_manifest = {
                "run_id": run_id,
                "status": "failed",
                "cleaning_version": CLEANING_VERSION,
                "started_at": started_at.isoformat(),
                "completed_at": completed_at.isoformat(),
                "duration_seconds": round(
                    (completed_at - started_at).total_seconds(), 3
                ),
                "error": {
                    "type": type(error).__name__,
                    "message": str(error),
                },
            }
            try:
                self.storage.save_cleaned_manifest(run_id, failure_manifest)
            except Exception:
                LOGGER.exception("Could not save cleaning failure manifest")
            raise

    def _write_source(
        self,
        run_id,
        source_name,
        documents,
        initial_rejections,
        source_counts,
        quarantine_counts,
        cleaned_outputs,
    ):
        clean, rejected = self._validate_and_deduplicate(documents)
        rejected = [*initial_rejections, *rejected]
        output_path = self.storage.save_cleaned_source(
            run_id, source_name, clean
        )
        self.storage.save_quarantine(run_id, source_name, rejected)
        source_counts[source_name] = len(clean)
        quarantine_counts[source_name] = len(rejected)
        cleaned_outputs[source_name] = str(output_path)
        LOGGER.info(
            "Cleaned %s: accepted=%d quarantined=%d output=%s",
            source_name,
            len(clean),
            len(rejected),
            output_path,
        )

    def _clean_memberships(
        self,
        run_id,
        source_counts,
        quarantine_counts,
        cleaned_outputs,
    ):
        raw = self.storage.load_source(run_id, "membership_plans")
        if isinstance(raw, dict):
            items = raw.items()
        elif isinstance(raw, list):
            items = enumerate(raw)
        else:
            raise ValueError("membership_plans must be an object or list")

        documents, normalization_failures = self._normalize_records(
            items,
            lambda plan_id, record: normalize_membership_plan(
                plan_id, record, run_id
            ),
        )
        return self._write_source(
            run_id,
            "membership_plans",
            documents,
            normalization_failures,
            source_counts,
            quarantine_counts,
            cleaned_outputs,
        )

    def _clean_calendar(
        self,
        run_id,
        source_counts,
        quarantine_counts,
        cleaned_outputs,
    ):
        raw = self.storage.load_source(run_id, "calendar_occurrences")
        if not isinstance(raw, dict):
            raise ValueError("calendar_occurrences must be an object")

        events = raw.get("events", [])
        if not isinstance(events, list):
            raise ValueError("calendar_occurrences.events must be a list")
        documents, normalization_failures = self._normalize_records(
            enumerate(events),
            lambda _, record: normalize_calendar_event(record, run_id),
        )
        failures = [
            {
                "errors": ["MEC occurrence fetch failed"],
                "record": failure,
            }
            for failure in raw.get("failures", [])
        ]
        return self._write_source(
            run_id,
            "calendar_occurrences",
            documents,
            [*failures, *normalization_failures],
            source_counts,
            quarantine_counts,
            cleaned_outputs,
        )

    @staticmethod
    def _normalize_records(items, normalizer):
        documents = []
        rejected = []

        for identity, record in items:
            try:
                if not isinstance(record, dict):
                    raise TypeError("record must be an object")
                documents.append(normalizer(identity, record))
            except (KeyError, TypeError, ValueError) as error:
                rejected.append({
                    "errors": [f"Normalization failed: {error}"],
                    "record": record,
                })

        return documents, rejected

    @staticmethod
    def _validate_and_deduplicate(documents):
        clean = []
        rejected = []
        seen = set()
        seen_content_hashes = set()

        for document in documents:
            errors = validate_document(document)
            document_id = document.get("document_id")
            if document_id in seen:
                errors.append("duplicate document_id")
            else:
                seen.add(document_id)

            content_hash = document.get("metadata", {}).get("content_hash")
            if content_hash:
                if content_hash in seen_content_hashes:
                    errors.append("duplicate cleaned content")
                else:
                    seen_content_hashes.add(content_hash)

            if errors:
                rejected.append({"errors": errors, "record": document})
            else:
                clean.append(document)

        return clean, rejected
