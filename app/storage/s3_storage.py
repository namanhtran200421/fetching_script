import json
import os

import boto3
from botocore.exceptions import ClientError, NoCredentialsError

class S3Storage:

    def __init__(
        self,
        bucket_name: str,
        s3_client=None
    ):
        if not bucket_name:
            raise ValueError("S3 bucket name is required")

        self.bucket_name = bucket_name
        self.s3_client = (
            s3_client
            if s3_client is not None
            else boto3.client("s3", region_name=os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "us-east-1")),)
        )

    def validate_connection(self):
        try:
            self.s3_client.head_bucket(
                Bucket=self.bucket_name
            )
        except NoCredentialsError as error:
            raise RuntimeError(
                "AWS credentials were not found. Configure "
                "boto3 credentials before using S3 storage."
            ) from error
        except ClientError as error:
            error_code = error.response.get(
                "Error",
                {}
            ).get("Code", "Unknown")

            raise RuntimeError(
                f"Cannot access S3 bucket "
                f"'{self.bucket_name}' "
                f"(AWS error {error_code})."
            ) from error

        return f"s3://{self.bucket_name}"

    def _save_json(self, key: str, data):
        body = json.dumps(
            data,
            ensure_ascii=False,
            indent=2
        ).encode("utf-8")

        try:
            self.s3_client.put_object(
                Bucket=self.bucket_name,
                Key=key,
                Body=body,
                ContentType="application/json"
            )
        except NoCredentialsError as error:
            raise RuntimeError(
                "AWS credentials were not found. Configure "
                "boto3 credentials before using S3 storage."
            ) from error
        except ClientError as error:
            error_code = error.response.get(
                "Error",
                {}
            ).get("Code", "Unknown")

            raise RuntimeError(
                f"Failed to upload s3://"
                f"{self.bucket_name}/{key} "
                f"(AWS error {error_code})."
            ) from error

        return f"s3://{self.bucket_name}/{key}"

    def save_source(
        self,
        run_id: str,
        source_name: str,
        data
    ):
        key = (
            f"raw/{run_id}/"
            f"{source_name}.json"
        )

        return self._save_json(
            key=key,
            data=data
        )

    def save_manifest(
        self,
        run_id: str,
        manifest: dict
    ):
        key = (
            f"raw/{run_id}/manifest.json"
        )

        return self._save_json(
            key=key,
            data=manifest
        )
