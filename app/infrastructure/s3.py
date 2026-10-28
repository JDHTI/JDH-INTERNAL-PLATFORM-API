"""S3-compatible object storage adapter (AWS S3 or local MinIO)."""

from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import quote

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
        public_endpoint_url: str = "",
    ) -> None:
        self.bucket = bucket

        def build(url: str) -> Any:
            return boto3.client(
                "s3",
                endpoint_url=url or None,
                region_name=region,
                aws_access_key_id=access_key_id,
                aws_secret_access_key=secret_access_key,
                config=Config(
                    signature_version="s3v4",
                    connect_timeout=connect_timeout,
                    read_timeout=connect_timeout,
                    retries={"max_attempts": 1},
                    s3={"addressing_style": "path"},
                ),
            )

        self.client = build(endpoint_url)
        # The signature covers the host: inside Compose the API reaches
        # minio:9000 while the browser reaches the published port.
        public = public_endpoint_url or endpoint_url
        self.presign_client = self.client if public == endpoint_url else build(public)

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

    def presigned_download_url(
        self, key: str, filename: str, expires_in: int, inline: bool = False
    ) -> str:
        """Sign a temporary GET URL that downloads the object as ``filename``.

        With ``inline`` the browser displays it (images, PDFs) instead of saving it.
        Signing is local (no network call), so it does not need a thread.
        """

        disposition = "inline" if inline else "attachment"
        return self.presign_client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self.bucket,
                "Key": key,
                "ResponseContentDisposition": f"{disposition}; filename*=UTF-8''{quote(filename)}",
            },
            ExpiresIn=expires_in,
        )

    def close(self) -> None:
        for client in {
            id(self.client): self.client,
            id(self.presign_client): self.presign_client,
        }.values():
            close = getattr(client, "close", None)
            if close:
                close()
