from pathlib import Path
from typing import BinaryIO

import boto3


class S3ArtifactStorage:
    def __init__(
        self, *, bucket: str, endpoint_url: str | None = None,
        access_key: str | None = None, secret_key: str | None = None,
        region: str | None = None,
    ) -> None:
        self.bucket = bucket
        self.client = boto3.client(
            "s3", endpoint_url=endpoint_url,
            aws_access_key_id=access_key, aws_secret_access_key=secret_key,
            region_name=region,
        )

    def put_once(self, key: str, source: Path) -> None:
        with source.open("rb") as handle:
            try:
                self.client.put_object(
                    Bucket=self.bucket, Key=key, Body=handle, IfNoneMatch="*"
                )
            except self.client.exceptions.ClientError as exc:
                status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
                if status not in {409, 412}:
                    raise

    def open(self, key: str) -> BinaryIO:
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"]

    def issue_download(self, key: str, expires_in_seconds: int) -> str:
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=expires_in_seconds,
        )
