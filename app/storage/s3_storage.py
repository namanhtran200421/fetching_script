import json

import boto3


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
        self.s3_client.head_bucket(Bucket=self.bucket_name)

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
