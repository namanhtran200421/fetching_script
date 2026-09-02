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


if __name__ == "__main__":
    unittest.main()
