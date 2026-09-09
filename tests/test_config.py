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
            patch.object(config, "RAW_S3_BUCKET_NAME", None),
            patch.object(config, "CLEANED_S3_BUCKET_NAME", None),
        ):
            with self.assertRaisesRegex(ValueError, "RAW_S3_BUCKET_NAME"):
                config.validate_config()

    def test_html_concurrency_must_be_positive(self):
        with patch.object(config, "HTML_CRAWL_CONCURRENCY", 0):
            with self.assertRaisesRegex(ValueError, "CONCURRENCY"):
                config.validate_config()

    def test_html_burst_size_must_be_positive(self):
        with patch.object(config, "HTML_CRAWL_BURST_SIZE", 0):
            with self.assertRaisesRegex(ValueError, "BURST_SIZE"):
                config.validate_config()

    def test_incremental_reconciliation_interval_must_be_positive(self):
        with patch.object(config, "FULL_RECONCILIATION_INTERVAL_DAYS", 0):
            with self.assertRaisesRegex(ValueError, "RECONCILIATION"):
                config.validate_config()


if __name__ == "__main__":
    unittest.main()
