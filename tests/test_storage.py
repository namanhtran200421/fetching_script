import json
import tempfile
import unittest
from pathlib import Path

from botocore.exceptions import ClientError

from app.storage.local_storage import LocalStorage
from app.storage.naming import source_file_stem
from app.storage.s3_storage import S3Storage
from app.storage.serialization import json_line


class FakeS3:
    def __init__(self):
        self.calls = []

    def head_bucket(self, **kwargs):
        self.calls.append(("head_bucket", kwargs))

    def put_object(self, **kwargs):
        self.calls.append(("put_object", kwargs))

    def get_object(self, **kwargs):
        self.calls.append(("get_object", kwargs))

        class Body:
            def read(self):
                return b'{"status":"complete"}'

        return {"Body": Body()}


class LegacyOnlyFakeS3(FakeS3):
    def get_object(self, **kwargs):
        self.calls.append(("get_object", kwargs))
        if kwargs["Key"].endswith("info-hub.json"):
            raise ClientError(
                {"Error": {"Code": "NoSuchKey", "Message": "missing"}},
                "GetObject",
            )

        class Body:
            def read(self):
                return b'[{"id":1}]'

        return {"Body": Body()}


class StorageTests(unittest.TestCase):
    def test_source_names_are_human_readable(self):
        self.assertEqual(source_file_stem("info_hub"), "info-hub")
        self.assertEqual(
            source_file_stem("mec_event_definitions"),
            "mec-event-definitions",
        )

    def test_local_storage_writes_valid_json_atomically(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = LocalStorage(temp_dir).save_manifest("run", {"ok": True})

            self.assertEqual(json.loads(Path(path).read_text()), {"ok": True})
            self.assertFalse(Path(str(path) + ".tmp").exists())

    def test_local_run_report_is_a_separate_text_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = LocalStorage(temp_dir).save_run_report(
                "run",
                "pipeline started\npipeline completed\n",
            )

            self.assertEqual(path.name, "run-report.log")
            self.assertEqual(
                path.read_text(encoding="utf-8"),
                "pipeline started\npipeline completed\n",
            )

    def test_local_storage_uses_readable_name_and_loads_legacy_name(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = LocalStorage(temp_dir)
            path = storage.save_source("new-run", "info_hub", [{"id": 1}])
            self.assertEqual(path.name, "info-hub.json")

            legacy_dir = Path(temp_dir) / "old-run"
            legacy_dir.mkdir()
            (legacy_dir / "info_hub.json").write_text(
                '[{"id": 2}]', encoding="utf-8"
            )
            self.assertEqual(
                storage.load_source("old-run", "info_hub"),
                [{"id": 2}],
            )

    def test_local_storage_publishes_and_loads_raw_latest_pointer(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = LocalStorage(temp_dir)
            manifest = storage.save_manifest("run", {"status": "complete"})

            pointer_path = storage.publish_raw_latest(
                "run", manifest, status="complete"
            )

            self.assertEqual(pointer_path, Path(temp_dir) / "latest.json")
            self.assertEqual(storage.load_raw_latest(), {
                "run_id": "run",
                "manifest": str(manifest),
                "status": "complete",
            })

    def test_s3_storage_validates_and_encrypts_upload(self):
        fake = FakeS3()
        storage = S3Storage("bucket", fake)

        self.assertEqual(storage.validate_connection(), {
            "raw": "s3://bucket",
            "cleaned": "s3://bucket",
        })
        location = storage.save_manifest("run", {"ok": True})

        self.assertEqual(location, "s3://bucket/raw/run/manifest.json")
        upload = fake.calls[1][1]
        self.assertEqual(upload["ServerSideEncryption"], "AES256")
        self.assertEqual(json.loads(upload["Body"]), {"ok": True})

    def test_s3_storage_reads_raw_and_writes_cleaned_to_separate_bucket(self):
        fake = FakeS3()
        storage = S3Storage(
            "raw-bucket",
            fake,
            cleaned_bucket_name="cleaned-bucket",
        )

        self.assertEqual(storage.load_manifest("run"), {"status": "complete"})
        location = storage.save_cleaned_source(
            "run", "info_hub", [{"content_text": "hello"}]
        )

        self.assertEqual(
            location,
            "s3://cleaned-bucket/cleaned/run/info-hub.jsonl",
        )
        upload = fake.calls[1][1]
        self.assertEqual(upload["ContentType"], "application/x-ndjson")
        self.assertEqual(upload["Bucket"], "cleaned-bucket")
        self.assertEqual(upload["Body"], b'{"content_text": "hello"}\n')

        pointer = storage.publish_cleaned_latest(
            "run", "s3://cleaned-bucket/cleaned/run/manifest.json"
        )
        self.assertEqual(pointer, "s3://cleaned-bucket/cleaned/latest.json")

    def test_s3_storage_loads_legacy_underscore_name(self):
        fake = LegacyOnlyFakeS3()
        storage = S3Storage("bucket", fake)

        self.assertEqual(storage.load_source("old-run", "info_hub"), [{"id": 1}])
        requested_keys = [
            call[1]["Key"] for call in fake.calls if call[0] == "get_object"
        ]
        self.assertEqual(
            requested_keys,
            [
                "raw/old-run/info-hub.json",
                "raw/old-run/info_hub.json",
            ],
        )

    def test_s3_storage_publishes_raw_latest_pointer(self):
        fake = FakeS3()
        storage = S3Storage("bucket", fake)

        location = storage.publish_raw_latest(
            "run", "s3://bucket/raw/run/manifest.json", status="complete"
        )

        self.assertEqual(location, "s3://bucket/raw/latest.json")
        upload = fake.calls[0][1]
        self.assertEqual(upload["Key"], "raw/latest.json")
        self.assertEqual(json.loads(upload["Body"])["run_id"], "run")

    def test_s3_run_report_is_saved_beside_raw_manifest(self):
        fake = FakeS3()
        storage = S3Storage("bucket", fake)

        location = storage.save_run_report("run", "complete log\n")

        self.assertEqual(
            location,
            "s3://bucket/raw/run/run-report.log",
        )
        upload = fake.calls[0][1]
        self.assertEqual(upload["Key"], "raw/run/run-report.log")
        self.assertEqual(upload["ContentType"], "text/plain; charset=utf-8")
        self.assertEqual(upload["Body"], b"complete log\n")
        self.assertEqual(upload["ServerSideEncryption"], "AES256")


class JsonLineTests(unittest.TestCase):
    """One cleaned document must always occupy exactly one output line."""

    separators = "\u2028\u2029\u0085"

    def test_unicode_line_breaks_are_escaped(self):
        row = {"content_html": f"<p>a{self.separators}b</p>"}

        line = json_line(row)

        self.assertEqual(len(line.splitlines()), 1)
        self.assertEqual(len(line.split("\n")), 1)
        # Escaping is a serialization detail: the parsed value is unchanged.
        self.assertEqual(json.loads(line), row)

    def test_ordinary_unicode_is_still_written_unescaped(self):
        line = json_line({"title": "Cultural Infusion – Atlas ’26"})

        self.assertIn("Cultural Infusion – Atlas ’26", line)

    def test_written_jsonl_file_is_one_line_per_document(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = LocalStorage(Path(temp_dir) / "raw")
            documents = [
                {"document_id": "a", "content_html": f"x{self.separators}y"},
                {"document_id": "b", "content_html": "plain"},
            ]

            path = storage.save_cleaned_source("run", "atlas_posts", documents)

            raw = Path(path).read_text(encoding="utf-8")
            self.assertEqual(len(raw.splitlines()), 2)
            self.assertEqual(
                [json.loads(line) for line in raw.splitlines()], documents
            )


if __name__ == "__main__":
    unittest.main()
