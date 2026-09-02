import json

import boto3
from botocore.exceptions import BotoCoreError, ClientError


class S3Storage:

    def __init__(self, bucket_name: str, s3_client=None):
        if not bucket_name:
            raise ValueError("S3 bucket name is required")

        self.bucket_name = bucket_name

        # Let boto3 find the AWS login.
        self.s3_client = (
            s3_client or boto3.client("s3")
        )

    def validate_connection(self):
        # Make sure the bucket works before fetching data.
        try:
            self.s3_client.head_bucket(Bucket=self.bucket_name)
        except ClientError as error:
            error_code = error.response.get("Error", {}).get("Code", "Unknown")
            raise RuntimeError(
                f"Cannot access S3 bucket {self.bucket_name!r} "
                f"(AWS error {error_code}). Check the EC2 role and bucket policy."
            ) from error
        except BotoCoreError as error:
            raise RuntimeError(
                "AWS credentials are unavailable or expired. On EC2, attach an "
                "instance role; for local runs, refresh the configured AWS login."
            ) from error

        return f"s3://{self.bucket_name}"

    def _save_json(self, key: str, data):
        # Turn the data into JSON and send it to S3.
        body = json.dumps(data, ensure_ascii=False)

        self.s3_client.put_object(
            Bucket=self.bucket_name,
            Key=key,
            Body=body.encode("utf-8"),
            ContentType="application/json",
            ServerSideEncryption="AES256"
        )

        return f"s3://{self.bucket_name}/{key}"

    def save_source(self, run_id: str, source_name: str, data):
        return self._save_json(
            f"raw/{run_id}/{source_name}.json",
            data
        )

    def save_manifest(self, run_id: str, manifest: dict):
        return self._save_json(
            f"raw/{run_id}/manifest.json",
            manifest
        )

    def _load_json(self, key: str):
        response = self.s3_client.get_object(
            Bucket=self.bucket_name,
            Key=key,
        )
        return json.loads(response["Body"].read().decode("utf-8"))

    def load_manifest(self, run_id: str):
        return self._load_json(f"raw/{run_id}/manifest.json")

    def load_source(self, run_id: str, source_name: str):
        return self._load_json(f"raw/{run_id}/{source_name}.json")

    def save_curated_source(self, run_id: str, source_name: str, data):
        return self._save_json(
            f"curated/{run_id}/{source_name}.json",
            data,
        )

    def save_quarantine(self, run_id: str, source_name: str, data):
        return self._save_json(
            f"quarantine/{run_id}/{source_name}.json",
            data,
        )

    def save_curated_manifest(self, run_id: str, manifest: dict):
        return self._save_json(
            f"curated/{run_id}/manifest.json",
            manifest,
        )

    def _save_jsonl(self, key: str, rows):
        body = "".join(
            json.dumps(row, ensure_ascii=False) + "\n"
            for row in rows
        )
        self.s3_client.put_object(
            Bucket=self.bucket_name,
            Key=key,
            Body=body.encode("utf-8"),
            ContentType="application/x-ndjson",
            ServerSideEncryption="AES256",
        )
        return f"s3://{self.bucket_name}/{key}"

    def save_rag_chunks(self, run_id: str, source_name: str, chunks):
        return self._save_jsonl(
            f"rag/{run_id}/{source_name}.jsonl",
            chunks,
        )

    def save_rag_manifest(self, run_id: str, manifest: dict):
        return self._save_json(
            f"rag/{run_id}/manifest.json",
            manifest,
        )
