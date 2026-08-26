import json

import boto3


class S3Storage:

    def __init__(
        self,
        bucket_name: str
    ):
        self.bucket_name = bucket_name
        self.s3_client = boto3.client("s3")

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

        body = json.dumps(
            data,
            ensure_ascii=False,
            indent=2
        )

        self.s3_client.put_object(
            Bucket=self.bucket_name,
            Key=key,
            Body=body.encode("utf-8"),
            ContentType="application/json"
        )

        return f"s3://{self.bucket_name}/{key}"

    def save_manifest(
        self,
        run_id: str,
        manifest: dict
    ):
        key = (
            f"raw/{run_id}/manifest.json"
        )

        body = json.dumps(
            manifest,
            ensure_ascii=False,
            indent=2
        )

        self.s3_client.put_object(
            Bucket=self.bucket_name,
            Key=key,
            Body=body.encode("utf-8"),
            ContentType="application/json"
        )

        return f"s3://{self.bucket_name}/{key}"
