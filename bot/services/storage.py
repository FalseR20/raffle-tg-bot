"""Private RustFS (S3-compatible) receipt storage."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import aioboto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from bot.logging import get_logger
from bot.services.errors import StorageError

logger = get_logger(__name__)


class ReceiptStorage:
    """Thin async wrapper around aioboto3 S3.

    Production must use TLS and unique credentials. Bucket access stays
    private; no public URLs are generated.
    """

    def __init__(
        self,
        *,
        endpoint: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        region: str = "us-east-1",
        use_ssl: bool = True,
        force_path_style: bool = True,
    ) -> None:
        self.endpoint = endpoint
        self.bucket = bucket
        self._access_key = access_key
        self._secret_key = secret_key
        self._region = region
        self._session = aioboto3.Session()
        self._config = Config(
            signature_version="s3v4",
            s3={"addressing_style": "path" if force_path_style else "virtual"},
            retries={"max_attempts": 3, "mode": "standard"},
            connect_timeout=10,
            read_timeout=60,
        )
        self._use_ssl = use_ssl

    @asynccontextmanager
    async def _client(self) -> AsyncIterator[Any]:
        async with self._session.client(
            "s3",
            endpoint_url=self.endpoint,
            aws_access_key_id=self._access_key,
            aws_secret_access_key=self._secret_key,
            region_name=self._region,
            use_ssl=self._use_ssl,
            config=self._config,
        ) as client:
            yield client

    async def ensure_bucket(self) -> None:
        try:
            async with self._client() as client:
                try:
                    await client.head_bucket(Bucket=self.bucket)
                except ClientError as exc:
                    code = str(exc.response.get("Error", {}).get("Code", ""))
                    if code not in {"404", "NoSuchBucket", "NotFound"}:
                        raise
                    await client.create_bucket(Bucket=self.bucket)
                    logger.info("Created receipt bucket %s", self.bucket)
                try:
                    await client.put_public_access_block(
                        Bucket=self.bucket,
                        PublicAccessBlockConfiguration={
                            "BlockPublicAcls": True,
                            "IgnorePublicAcls": True,
                            "BlockPublicPolicy": True,
                            "RestrictPublicBuckets": True,
                        },
                    )
                except ClientError as exc:
                    logger.warning("Could not set public access block: %s", exc)
        except (BotoCoreError, ClientError) as exc:
            raise StorageError(f"RustFS is unavailable: {exc}") from exc

    async def healthcheck(self) -> bool:
        try:
            async with self._client() as client:
                await client.head_bucket(Bucket=self.bucket)
            return True
        except (BotoCoreError, ClientError):
            return False

    async def put(self, key: str, data: bytes, *, content_type: str) -> str | None:
        try:
            async with self._client() as client:
                response = await client.put_object(
                    Bucket=self.bucket,
                    Key=key,
                    Body=data,
                    ContentType=content_type,
                )
        except (BotoCoreError, ClientError) as exc:
            raise StorageError(f"Could not upload receipt: {exc}") from exc
        etag = response.get("ETag")
        return etag.strip('"') if isinstance(etag, str) else None

    async def get(self, key: str) -> bytes:
        try:
            async with self._client() as client:
                response = await client.get_object(Bucket=self.bucket, Key=key)
                body = response["Body"]
                return await body.read()
        except (BotoCoreError, ClientError) as exc:
            raise StorageError(f"Could not download receipt: {exc}") from exc

    async def delete(self, key: str) -> None:
        try:
            async with self._client() as client:
                await client.delete_object(Bucket=self.bucket, Key=key)
        except (BotoCoreError, ClientError) as exc:
            raise StorageError(f"Could not delete receipt: {exc}") from exc

    async def exists(self, key: str) -> bool:
        try:
            async with self._client() as client:
                await client.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise StorageError(f"Could not check receipt: {exc}") from exc
