import logging
from datetime import datetime, timezone

from app.cleaning.chunking import document_to_chunks
from app.cleaning.normalizers import (
    normalize_calendar_event,
    normalize_membership_plan,
    normalize_wordpress_record,
)
from app.cleaning.validators import validate_document
from app.ingestion.source_registry import WORDPRESS_SOURCES


LOGGER = logging.getLogger(__name__)
CLEANING_VERSION = "1.0"


class CleaningPipeline:
    def __init__(self, storage, chunk_size: int, chunk_overlap: int):
        self.storage = storage
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    def clean_run(self, run_id: str):
        started_at = datetime.now(timezone.utc)
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

        source_counts = {}
        quarantine_counts = {}
        chunk_counts = {}
        all_chunks = []

        try:
            for source_name, source_config in WORDPRESS_SOURCES.items():
                raw_records = self.storage.load_source(run_id, source_name)
                if not isinstance(raw_records, list):
                    raise ValueError(f"Raw source {source_name} must be a list")

                documents, normalization_failures = self._normalize_records(
                    enumerate(raw_records),
                    lambda _, record: normalize_wordpress_record(
                        record, source_name, source_config, run_id
                    ),
                )
                chunks = self._write_source(
                    run_id,
                    source_name,
                    documents,
                    normalization_failures,
                    source_counts,
                    quarantine_counts,
                    chunk_counts,
                )
                all_chunks.extend(chunks)

            membership_chunks = self._clean_memberships(
                run_id,
                source_counts,
                quarantine_counts,
                chunk_counts,
            )
            all_chunks.extend(membership_chunks)

            calendar_chunks = self._clean_calendar(
                run_id,
                source_counts,
                quarantine_counts,
                chunk_counts,
            )
            all_chunks.extend(calendar_chunks)

            self.storage.save_rag_chunks(run_id, "chunks", all_chunks)
            total_quarantined = sum(quarantine_counts.values())
            completed_at = datetime.now(timezone.utc).isoformat()
            manifest = {
                "run_id": run_id,
                "status": (
                    "complete_with_errors" if total_quarantined else "complete"
                ),
                "cleaning_version": CLEANING_VERSION,
                "started_at": started_at.isoformat(),
                "completed_at": completed_at,
                "raw_manifest_status": raw_status,
                "source_counts": source_counts,
                "quarantine_counts": quarantine_counts,
                "chunk_counts": chunk_counts,
                "total_documents": sum(source_counts.values()),
                "total_quarantined": total_quarantined,
                "total_chunks": len(all_chunks),
                "rag_output": f"rag/{run_id}/chunks.jsonl",
                "chunking": {
                    "strategy": "paragraph-aware-character",
                    "size_chars": self.chunk_size,
                    "overlap_chars": self.chunk_overlap,
                },
            }
            self.storage.save_curated_manifest(run_id, manifest)
            self.storage.save_rag_manifest(run_id, manifest)
            LOGGER.info(
                "Cleaned run %s into %d documents and %d RAG chunks",
                run_id,
                manifest["total_documents"],
                manifest["total_chunks"],
            )
            return manifest
        except Exception as error:
            failure_manifest = {
                "run_id": run_id,
                "status": "failed",
                "cleaning_version": CLEANING_VERSION,
                "started_at": started_at.isoformat(),
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "error": {
                    "type": type(error).__name__,
                    "message": str(error),
                },
            }
            try:
                self.storage.save_curated_manifest(run_id, failure_manifest)
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
        chunk_counts,
    ):
        clean, rejected = self._validate_and_deduplicate(documents)
        rejected = [*initial_rejections, *rejected]
        chunks = [
            chunk
            for document in clean
            for chunk in document_to_chunks(
                document,
                self.chunk_size,
                self.chunk_overlap,
            )
        ]

        self.storage.save_curated_source(run_id, source_name, clean)
        self.storage.save_quarantine(run_id, source_name, rejected)
        self.storage.save_rag_chunks(run_id, source_name, chunks)
        source_counts[source_name] = len(clean)
        quarantine_counts[source_name] = len(rejected)
        chunk_counts[source_name] = len(chunks)
        return chunks

    def _clean_memberships(
        self,
        run_id,
        source_counts,
        quarantine_counts,
        chunk_counts,
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
            chunk_counts,
        )

    def _clean_calendar(
        self,
        run_id,
        source_counts,
        quarantine_counts,
        chunk_counts,
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
            chunk_counts,
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

        for document in documents:
            errors = validate_document(document)
            document_id = document.get("document_id")
            if document_id in seen:
                errors.append("duplicate document_id")
            else:
                seen.add(document_id)

            if errors:
                rejected.append({"errors": errors, "record": document})
            else:
                clean.append(document)

        return clean, rejected
