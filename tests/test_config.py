import unittest
from unittest.mock import patch

from app import config


class ConfigTests(unittest.TestCase):
    def test_reports_all_missing_required_values(self):
        with (
            patch.object(config, "WORDPRESS_BASE_URL", None),
            patch.object(config, "WORDPRESS_USERNAME", None),
            patch.object(config, "WORDPRESS_APP_PASSWORD", None),
            patch.object(config, "MEC_API_TOKEN", None),
            patch.object(config, "STORAGE_MODE", "local"),
        ):
            with self.assertRaises(ValueError) as caught:
                config.validate_config()

        message = str(caught.exception)
        self.assertIn("WORDPRESS_BASE_URL is required", message)
        self.assertIn("WORDPRESS_USERNAME is required", message)
        self.assertIn("WORDPRESS_APP_PASSWORD is required", message)
        self.assertIn("MEC_API_TOKEN is required", message)

    def test_s3_requires_bucket(self):
        with (
            patch.object(config, "STORAGE_MODE", "s3"),
            patch.object(config, "S3_BUCKET_NAME", None),
        ):
            with self.assertRaisesRegex(ValueError, "S3_BUCKET_NAME is required"):
                config.validate_config()

    def test_rag_overlap_must_be_smaller_than_chunk(self):
        with (
            patch.object(config, "RAG_CHUNK_SIZE_CHARS", 500),
            patch.object(config, "RAG_CHUNK_OVERLAP_CHARS", 500),
        ):
            with self.assertRaisesRegex(ValueError, "must be smaller"):
                config.validate_cleaning_config()


if __name__ == "__main__":
    unittest.main()
