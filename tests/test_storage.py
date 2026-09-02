import json
import tempfile
import unittest
from pathlib import Path

from app.storage.local_storage import LocalStorage
from app.storage.s3_storage import S3Storage


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


class StorageTests(unittest.TestCase):
    def test_local_storage_writes_valid_json_atomically(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = LocalStorage(temp_dir).save_manifest("run", {"ok": True})

            self.assertEqual(json.loads(Path(path).read_text()), {"ok": True})
            self.assertFalse(Path(str(path) + ".tmp").exists())

    def test_s3_storage_validates_and_encrypts_upload(self):
        fake = FakeS3()
        storage = S3Storage("bucket", fake)

        self.assertEqual(storage.validate_connection(), "s3://bucket")
        location = storage.save_manifest("run", {"ok": True})

        self.assertEqual(location, "s3://bucket/raw/run/manifest.json")
        upload = fake.calls[1][1]
        self.assertEqual(upload["ServerSideEncryption"], "AES256")
        self.assertEqual(json.loads(upload["Body"]), {"ok": True})

    def test_s3_storage_reads_raw_and_writes_rag_jsonl(self):
        fake = FakeS3()
        storage = S3Storage("bucket", fake)

        self.assertEqual(storage.load_manifest("run"), {"status": "complete"})
        location = storage.save_rag_chunks("run", "chunks", [{"text": "hello"}])

        self.assertEqual(location, "s3://bucket/rag/run/chunks.jsonl")
        upload = fake.calls[1][1]
        self.assertEqual(upload["ContentType"], "application/x-ndjson")
        self.assertEqual(upload["Body"], b'{"text": "hello"}\n')


if __name__ == "__main__":
    unittest.main()
