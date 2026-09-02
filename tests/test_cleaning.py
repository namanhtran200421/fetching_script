import json
import tempfile
import unittest
from pathlib import Path

from app.cleaning.chunking import document_to_chunks, split_text
from app.cleaning.normalizers import html_to_text
from app.cleaning.pipeline import CleaningPipeline
from app.ingestion.source_registry import WORDPRESS_SOURCES
from app.storage.local_storage import LocalStorage


class CleaningTests(unittest.TestCase):
    def test_html_to_text_removes_markup_and_ignored_content(self):
        value = "<h2>Title &amp; more</h2><script>bad()</script><p>Hello<br>world</p>"

        self.assertEqual(html_to_text(value), "Title & more\nHello\nworld")

    def test_chunks_are_bounded_and_stable(self):
        text = "First paragraph. " * 30
        chunks = split_text(text, chunk_size=200, overlap=20)
        document = {
            "schema_version": "1.0",
            "document_id": "research:1",
            "run_id": "run",
            "source": "research",
            "source_id": 1,
            "content_type": "research",
            "access_class": "member_restricted",
            "title": "Title",
            "content_text": text,
            "url": "https://example.test/1",
            "modified_at": "2026-01-01T00:00:00",
            "metadata": {
                "safe": True,
                "nested": {"not": "vector-store-safe"},
            },
        }

        first = document_to_chunks(document, 200, 20)
        second = document_to_chunks(document, 200, 20)

        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= 200 for chunk in chunks))
        self.assertEqual(
            [chunk["chunk_id"] for chunk in first],
            [chunk["chunk_id"] for chunk in second],
        )
        self.assertEqual(first[0]["metadata"], {"safe": True})

    def test_pipeline_writes_curated_quarantine_and_rag_layers(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = LocalStorage(Path(temp_dir) / "raw")
            run_id = "test_run"
            storage.save_manifest(run_id, {"status": "complete"})

            valid_record = {
                "id": 1,
                "title": {"rendered": "Example &amp; title"},
                "content": {
                    "rendered": "<p>Useful retrieval content.</p>",
                    "protected": False,
                },
                "modified_gmt": "2026-01-01T00:00:00",
                "link": "https://example.test/document",
            }
            for source_name in WORDPRESS_SOURCES:
                records = [dict(valid_record)]
                if source_name == "research":
                    records.extend([
                        {"id": None, "title": {}, "content": {}},
                        "malformed record",
                    ])
                storage.save_source(run_id, source_name, records)

            storage.save_source(
                run_id,
                "membership_plans",
                {
                    "1": {
                        "id": "1",
                        "name": "Member",
                        "description": "<p>Member plan</p>",
                        "billing_amount": 10,
                    }
                },
            )
            storage.save_source(
                run_id,
                "calendar_occurrences",
                {
                    "events": [
                        {
                            "event_id": 1,
                            "response": {
                                "success": 1,
                                "occurrences": [
                                    {"start": "2026-01-01", "end": "2026-01-02"}
                                ],
                            },
                        }
                    ],
                    "failures": [],
                },
            )

            manifest = CleaningPipeline(storage, 300, 30).clean_run(run_id)

            self.assertEqual(manifest["status"], "complete_with_errors")
            self.assertEqual(manifest["quarantine_counts"]["research"], 2)
            self.assertGreater(manifest["total_chunks"], 0)

            curated_path = Path(temp_dir) / "curated" / run_id / "research.json"
            quarantine_path = (
                Path(temp_dir) / "quarantine" / run_id / "research.json"
            )
            rag_path = Path(temp_dir) / "rag" / run_id / "chunks.jsonl"
            curated = json.loads(curated_path.read_text())
            quarantined = json.loads(quarantine_path.read_text())
            rag_rows = [json.loads(line) for line in rag_path.read_text().splitlines()]

            self.assertEqual(curated[0]["content_text"], "Useful retrieval content.")
            self.assertEqual(len(quarantined), 2)
            self.assertEqual(len(rag_rows), manifest["total_chunks"])
            self.assertIn("chunk_id", rag_rows[0])
            self.assertIn("access_class", rag_rows[0])

    def test_pipeline_accepts_complete_legacy_manifest(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = LocalStorage(Path(temp_dir) / "raw")
            run_id = "legacy_run"
            storage.save_manifest(
                run_id,
                {"run_id": run_id, "source_counts": {}},
            )
            for source_name in WORDPRESS_SOURCES:
                storage.save_source(run_id, source_name, [])
            storage.save_source(run_id, "membership_plans", {})
            storage.save_source(
                run_id,
                "calendar_occurrences",
                {"events": [], "failures": []},
            )

            manifest = CleaningPipeline(storage, 300, 30).clean_run(run_id)

            self.assertEqual(manifest["raw_manifest_status"], "legacy_complete")


if __name__ == "__main__":
    unittest.main()
