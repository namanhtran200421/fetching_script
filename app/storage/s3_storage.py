import json

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from app.storage.naming import source_file_stem


class S3Storage:

    def __init__(
        self,
        bucket_name: str,
        s3_client=None,
        cleaned_bucket_name: str = None,
    ):
        if not bucket_name:
            raise ValueError("S3 bucket name is required")

        self.bucket_name = bucket_name
        self.cleaned_bucket_name = cleaned_bucket_name or bucket_name

        # Let boto3 find the AWS login.
        self.s3_client = (
            s3_client or boto3.client("s3")
        )

    def validate_connection(self):
        # Make sure the bucket works before fetching data.
        try:
            for bucket in dict.fromkeys(
                (self.bucket_name, self.cleaned_bucket_name)
            ):
                self.s3_client.head_bucket(Bucket=bucket)
        except ClientError as error:
            error_code = error.response.get("Error", {}).get("Code", "Unknown")
            raise RuntimeError(
                f"Cannot access S3 bucket {bucket!r} "
                f"(AWS error {error_code}). Check the EC2 role and bucket policy."
            ) from error
        except BotoCoreError as error:
            raise RuntimeError(
                "AWS credentials are unavailable or expired. On EC2, attach an "
                "instance role; for local runs, refresh the configured AWS login."
            ) from error

        return {
            "raw": f"s3://{self.bucket_name}",
            "cleaned": f"s3://{self.cleaned_bucket_name}",
        }

    def _save_json(self, key: str, data, bucket_name=None):
        # Turn the data into JSON and send it to S3.
        body = json.dumps(data, ensure_ascii=False)

        self.s3_client.put_object(
            Bucket=bucket_name or self.bucket_name,
            Key=key,
            Body=body.encode("utf-8"),
            ContentType="application/json",
            ServerSideEncryption="AES256"
        )

        return f"s3://{bucket_name or self.bucket_name}/{key}"

    def save_source(self, run_id: str, source_name: str, data):
        return self._save_json(
            f"raw/{run_id}/{source_file_stem(source_name)}.json",
            data
        )

    def save_manifest(self, run_id: str, manifest: dict):
        return self._save_json(
            f"raw/{run_id}/manifest.json",
            manifest
        )

    def save_run_report(self, run_id: str, report: str):
        key = f"raw/{run_id}/run-report.log"
        self.s3_client.put_object(
            Bucket=self.bucket_name,
            Key=key,
            Body=report.encode("utf-8"),
            ContentType="text/plain; charset=utf-8",
            ServerSideEncryption="AES256",
        )
        return f"s3://{self.bucket_name}/{key}"

    def publish_raw_latest(self, run_id: str, manifest_path, status=None):
        return self._save_json(
            "raw/latest.json",
            {
                "run_id": run_id,
                "manifest": manifest_path,
                "status": status,
            },
        )

    def _load_json(self, key: str):
        response = self.s3_client.get_object(
            Bucket=self.bucket_name,
            Key=key,
        )
        return json.loads(response["Body"].read().decode("utf-8"))

    def load_manifest(self, run_id: str):
        return self._load_json(f"raw/{run_id}/manifest.json")

    def load_raw_latest(self):
        try:
            return self._load_json("raw/latest.json")
        except ClientError as error:
            error_code = error.response.get("Error", {}).get("Code")
            if error_code in {"NoSuchKey", "404"}:
                return None
            raise

    def load_source(self, run_id: str, source_name: str):
        readable_key = (
            f"raw/{run_id}/{source_file_stem(source_name)}.json"
        )
        try:
            return self._load_json(readable_key)
        except ClientError as error:
            error_code = error.response.get("Error", {}).get("Code")
            if error_code not in {"NoSuchKey", "404"}:
                raise
            # Older runs used internal underscore-separated source keys.
            return self._load_json(
                f"raw/{run_id}/{source_name}.json"
            )

    def save_cleaned_source(self, run_id: str, source_name: str, documents):
        return self._save_jsonl(
            f"cleaned/{run_id}/{source_file_stem(source_name)}.jsonl",
            documents,
            bucket_name=self.cleaned_bucket_name,
        )

    def save_quarantine(self, run_id: str, source_name: str, data):
        return self._save_json(
            f"quarantine/{run_id}/{source_file_stem(source_name)}.json",
            data,
            bucket_name=self.cleaned_bucket_name,
        )

    def save_cleaned_manifest(self, run_id: str, manifest: dict):
        return self._save_json(
            f"cleaned/{run_id}/manifest.json",
            manifest,
            bucket_name=self.cleaned_bucket_name,
        )

    def publish_cleaned_latest(
        self, run_id: str, manifest_path, status=None
    ):
        return self._save_json(
            "cleaned/latest.json",
            {
                "run_id": run_id,
                "manifest": manifest_path,
                "status": status,
            },
            bucket_name=self.cleaned_bucket_name,
        )

    def _save_jsonl(self, key: str, rows, bucket_name=None):
        body = "".join(
            json.dumps(row, ensure_ascii=False) + "\n"
            for row in rows
        )
        self.s3_client.put_object(
            Bucket=bucket_name or self.bucket_name,
            Key=key,
            Body=body.encode("utf-8"),
            ContentType="application/x-ndjson",
            ServerSideEncryption="AES256",
        )
        return f"s3://{bucket_name or self.bucket_name}/{key}"
