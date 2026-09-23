import json
import hashlib
import tempfile
import unittest
from pathlib import Path

from app.cleaning.normalizers import (
    html_to_markdown,
    html_to_text,
    normalize_calendar_event,
    normalize_membership_plan,
    normalize_wordpress_record,
)
from app.cleaning.pipeline import CleaningPipeline
from app.ingestion.source_registry import (
    DOCUMENT_SOURCES,
    SOURCES,
    WORDPRESS_SOURCES,
)
from app.storage.local_storage import LocalStorage
from app.storage.naming import source_file_stem


VALID_WORDPRESS_RECORD = {
    "id": 1,
    "title": {"rendered": "Example &amp; title"},
    "content": {
        "rendered": "<p>Useful retrieval content.</p>",
        "protected": False,
    },
    "modified_gmt": "2026-01-01T00:00:00",
    "link": "https://example.test/document",
}
VALID_MEDIA_RECORD = {
    "id": 1,
    "title": {"rendered": "Example &amp; title"},
    "mime_type": "application/pdf",
    "source_url": "https://example.test/report.pdf",
    "link": "https://example.test/report/",
    "modified_gmt": "2026-01-01T00:00:00",
    "content_text": "Useful retrieval content.",
    "page_count": 3,
}
VALID_TAXONOMY_RECORD = {
    "id": 175,
    "name": "Events",
    "slug": "events",
    "taxonomy": "category",
}


def sample_record_for(source_name):
    """Return a raw record shaped for this source's acquisition method."""
    method = SOURCES[source_name]["acquisition_method"]
    if method == "media_files":
        return dict(VALID_MEDIA_RECORD)
    if method == "taxonomy":
        return dict(VALID_TAXONOMY_RECORD)
    return dict(VALID_WORDPRESS_RECORD)


class CleaningTests(unittest.TestCase):
    def test_html_to_text_removes_markup_and_ignored_content(self):
        value = "<h2>Title &amp; more</h2><script>bad()</script><p>Hello<br>world</p>"

        self.assertEqual(html_to_text(value), "Title & more\nHello\nworld")

    def test_html_to_markdown_preserves_document_structure_and_links(self):
        value = """
        <nav>Repeated navigation</nav>
        <h2>Eligibility</h2>
        <p>Students can <strong>apply</strong>...</p>
        <ul><li>Must be enrolled</li><li>Must provide ID</li></ul>
        <ol><li>Complete the form</li><li>Submit it</li></ol>
        <blockquote>Applications close Friday.</blockquote>
        <p><a href="/papers/example.pdf">Download the paper</a></p>
        <div aria-hidden="true"><p>Collapsed but meaningful answer.</p></div>
        <script>bad()</script>
        """

        markdown = html_to_markdown(
            value, base_url="https://example.test/info-hub/eligibility/"
        )

        self.assertIn("## Eligibility", markdown)
        self.assertIn("Students can **apply**...", markdown)
        self.assertIn("- Must be enrolled\n- Must provide ID", markdown)
        self.assertIn("1. Complete the form\n2. Submit it", markdown)
        self.assertIn("> Applications close Friday.", markdown)
        self.assertIn(
            "[Download the paper](https://example.test/papers/example.pdf)",
            markdown,
        )
        self.assertIn("Collapsed but meaningful answer.", markdown)
        self.assertNotIn("Repeated navigation", markdown)
        self.assertNotIn("bad()", markdown)

    def test_all_normalizers_emit_the_three_content_representations(self):
        source_config = WORDPRESS_SOURCES["info_hub"]
        html = (
            "<h2>Eligibility</h2><p>Students can apply.</p>"
            "<ul><li>Must be enrolled</li></ul>"
        )
        wordpress = normalize_wordpress_record(
            {
                "id": 28181,
                "title": {"rendered": "Accessibility for Businesses"},
                "content": {"rendered": html, "protected": False},
                "link": "https://example.test/info-hub/accessibility/",
            },
            "info_hub",
            source_config,
            "run",
        )
        membership = normalize_membership_plan(
            "1",
            {
                "id": "1",
                "name": "Member",
                "description": "<h2>Benefits</h2><ul><li>Resources</li></ul>",
                "billing_amount": 10,
            },
            "run",
        )
        calendar = normalize_calendar_event(
            {
                "event_id": 7,
                "response": {
                    "success": 1,
                    "occurrences": [
                        {"start": "2026-01-01", "end": "2026-01-02"}
                    ],
                },
            },
            "run",
        )

        for document in (wordpress, membership, calendar):
            self.assertEqual(document["schema_version"], "1.1")
            self.assertEqual(document["content_format"], "text/markdown")
            self.assertIn("content_html", document)
            self.assertTrue(document["content_text"])
            self.assertTrue(document["content_markdown"].startswith("# "))
            expected_hash = hashlib.sha256(
                document["content_markdown"].encode("utf-8")
            ).hexdigest()
            self.assertEqual(document["metadata"]["content_hash"], expected_hash)

        self.assertEqual(wordpress["content_html"], html)
        self.assertIn("## Eligibility", wordpress["content_markdown"])
        self.assertIn("- Must be enrolled", wordpress["content_markdown"])
        self.assertIn("## Membership details", membership["content_markdown"])
        self.assertIn("## Occurrences", calendar["content_markdown"])

    def test_research_normalization_infers_numbered_section_headings(self):
        document = normalize_wordpress_record(
            {
                "id": 42,
                "title": {"rendered": "Workforce Wellbeing"},
                "content": {
                    "rendered": (
                        "<p>1. INTRODUCTION The wellbeing of employees is an "
                        "important organisational concern.</p>"
                        "<p>2. LITERATURE REVIEW Workforce wellbeing has been "
                        "studied across multiple disciplines.</p>"
                        "<p>2.1 Conceptual Perspectives</p>"
                        "<p>This subsection describes the main frameworks.</p>"
                    )
                },
                "link": "https://example.test/academic-papers/wellbeing/",
            },
            "research",
            WORDPRESS_SOURCES["research"],
            "run",
        )

        self.assertIn("## 1. INTRODUCTION\n\nThe wellbeing", document["content_markdown"])
        self.assertIn(
            "## 2. LITERATURE REVIEW\n\nWorkforce wellbeing",
            document["content_markdown"],
        )
        self.assertIn("### 2.1 Conceptual Perspectives", document["content_markdown"])

    def test_pipeline_writes_full_cleaned_documents_without_chunks(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = LocalStorage(Path(temp_dir) / "raw")
            run_id = "test_run"
            storage.save_manifest(run_id, {"status": "complete"})

            for source_name in WORDPRESS_SOURCES:
                records = [sample_record_for(source_name)]
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

            manifest = CleaningPipeline(storage).clean_run(run_id)

            self.assertEqual(manifest["status"], "complete_with_errors")
            self.assertGreaterEqual(manifest["duration_seconds"], 0)
            self.assertEqual(manifest["quarantine_counts"]["research"], 2)
            self.assertNotIn("total_chunks", manifest)
            self.assertEqual(manifest["document_schema_version"], "1.1")
            self.assertEqual(manifest["canonical_content_field"], "content_markdown")
            self.assertEqual(manifest["content_format"], "text/markdown")

            cleaned_path = Path(temp_dir) / "cleaned" / run_id / "research.jsonl"
            quarantine_path = (
                Path(temp_dir) / "quarantine" / run_id / "research.json"
            )
            cleaned = [
                json.loads(line)
                for line in cleaned_path.read_text().splitlines()
            ]
            quarantined = json.loads(quarantine_path.read_text())

            self.assertEqual(cleaned[0]["content_text"], "Useful retrieval content.")
            self.assertEqual(cleaned[0]["content_html"], "<p>Useful retrieval content.</p>")
            self.assertEqual(
                cleaned[0]["content_markdown"],
                "# Example & title\n\nUseful retrieval content.",
            )
            self.assertEqual(cleaned[0]["content_format"], "text/markdown")
            self.assertEqual(len(quarantined), 2)
            self.assertFalse((Path(temp_dir) / "rag" / run_id).exists())

            for source_name in DOCUMENT_SOURCES:
                source_path = (
                    Path(temp_dir)
                    / "cleaned"
                    / run_id
                    / f"{source_file_stem(source_name)}.jsonl"
                )
                source_document = json.loads(source_path.read_text().splitlines()[0])
                self.assertIn("content_html", source_document)
                self.assertTrue(source_document["content_text"])
                self.assertTrue(source_document["content_markdown"])

            # Reference sources are fetched and stored, but never cleaned
            # into documents.
            self.assertNotIn("atlas_taxonomies", manifest["source_counts"])
            self.assertEqual(
                manifest["reference_sources"], ["atlas_taxonomies"]
            )

            latest = json.loads(
                (Path(temp_dir) / "cleaned" / "latest.json").read_text()
            )
            self.assertEqual(latest["status"], "complete_with_errors")
            self.assertFalse(manifest["quality_gate_passed"])

    def test_unicode_line_separators_never_split_a_jsonl_record(self):
        # U+2028 and friends are line breaks to a Unicode-aware reader but
        # are not escaped by json.dumps, so one document would otherwise
        # occupy two lines of the output file.
        document = normalize_wordpress_record(
            {
                "id": 1,
                "title": {"rendered": "Title"},
                "content": {
                    "rendered": "<p>First\u2028second\u2029third\u0085fourth</p>"
                },
                "link": "https://atlas.example.test/one/",
            },
            "atlas_posts",
            SOURCES["atlas_posts"],
            "run-1",
        )

        for field in ("content_markdown", "content_text", "title"):
            with self.subTest(field=field):
                self.assertEqual(
                    len(document[field].splitlines()),
                    len(document[field].split("\n")),
                )

    def test_taxonomy_terms_resolve_to_names_on_public_posts(self):
        document = normalize_wordpress_record(
            {
                "id": 66020,
                "title": {"rendered": "Cultural Data Infrastructure"},
                "content": {"rendered": "<p>Body.</p>"},
                "link": "https://atlas.example.test/cultural-data/",
                "categories": [175, 9],
                "tags": [663, 4040],
            },
            "atlas_posts",
            SOURCES["atlas_posts"],
            "run-1",
            taxonomy_terms={
                "category": {
                    175: {"id": 175, "name": "Events"},
                    9: {"id": 9, "name": "Insights"},
                },
                "post_tag": {663: {"id": 663, "name": "Research"}},
            },
        )

        self.assertEqual(
            document["metadata"]["category_names"], ["Events", "Insights"]
        )
        # An ID with no matching term is dropped rather than guessed at.
        self.assertEqual(document["metadata"]["tag_names"], ["Research"])

    def test_records_without_taxonomy_terms_are_unchanged(self):
        document = normalize_wordpress_record(
            {
                "id": 1,
                "title": {"rendered": "Title"},
                "content": {"rendered": "<p>Body.</p>"},
                "link": "https://atlas.example.test/one/",
                "categories": [175],
            },
            "atlas_posts",
            SOURCES["atlas_posts"],
            "run-1",
        )

        self.assertNotIn("category_names", document["metadata"])

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

            manifest = CleaningPipeline(storage).clean_run(run_id)

            self.assertEqual(manifest["raw_manifest_status"], "legacy_complete")
            latest = json.loads(
                (Path(temp_dir) / "cleaned" / "latest.json").read_text()
            )
            self.assertEqual(latest["run_id"], run_id)

    def test_pipeline_can_reclean_run_created_before_new_source(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = LocalStorage(Path(temp_dir) / "raw")
            run_id = "older_run"
            old_sources = {
                name: 0 for name in WORDPRESS_SOURCES
                if name != "website_sitemap"
            }
            storage.save_manifest(run_id, {
                "run_id": run_id,
                "status": "complete",
                "source_counts": old_sources,
            })
            for source_name in old_sources:
                storage.save_source(run_id, source_name, [])
            storage.save_source(run_id, "membership_plans", [])
            storage.save_source(
                run_id,
                "calendar_occurrences",
                {"events": [], "failures": []},
            )

            manifest = CleaningPipeline(storage).clean_run(run_id)

            self.assertEqual(
                manifest["unavailable_sources"], ["website_sitemap"]
            )


if __name__ == "__main__":
    unittest.main()
