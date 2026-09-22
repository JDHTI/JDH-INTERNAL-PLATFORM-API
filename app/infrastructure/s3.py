"""S3-compatible object storage adapter (AWS S3 or local MinIO)."""

from __future__ import annotations

import asyncio
from typing import Any

import boto3
from botocore.client import Config


class S3Storage:
    """Keep the blocking boto3 client behind an async application boundary."""

    def __init__(
        self,
        endpoint_url: str,
        region: str,
        access_key_id: str,
        secret_access_key: str,
        bucket: str,
        connect_timeout: float = 2.0,
    ) -> None:
        self.bucket = bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint_url or None,
            region_name=region,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            config=Config(
                connect_timeout=connect_timeout,
                read_timeout=connect_timeout,
                retries={"max_attempts": 1},
                s3={"addressing_style": "path"},
            ),
        )

    async def check(self) -> None:
        """Verify that the configured bucket exists and is accessible."""

        await asyncio.to_thread(self.client.head_bucket, Bucket=self.bucket)

    async def put_object(self, key: str, body: bytes, content_type: str | None = None) -> None:
        kwargs: dict[str, Any] = {"Bucket": self.bucket, "Key": key, "Body": body}
        if content_type:
            kwargs["ContentType"] = content_type
        await asyncio.to_thread(self.client.put_object, **kwargs)

    async def get_object(self, key: str) -> bytes:
        response = await asyncio.to_thread(self.client.get_object, Bucket=self.bucket, Key=key)
        return await asyncio.to_thread(response["Body"].read)

    async def delete_object(self, key: str) -> None:
        await asyncio.to_thread(self.client.delete_object, Bucket=self.bucket, Key=key)

    def close(self) -> None:
        close = getattr(self.client, "close", None)
        if close:
            close()
