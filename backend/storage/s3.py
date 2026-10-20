"""S3-compatible storage. Cloudflare R2 is the intended production target."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path


class S3Storage:
    name = "r2"

    def __init__(self, endpoint_url: str | None, bucket: str, access_key: str, secret_key: str, region: str):
        import boto3
        from botocore.config import Config

        self.bucket = bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
            config=Config(signature_version="s3v4"),
        )

    def ensure_ready(self) -> None:
        return None

    def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)

    def put_file(self, key: str, path: Path, content_type: str) -> None:
        """Multipart upload straight from disk (no full read into memory)."""
        self.client.upload_file(str(path), self.bucket, key, ExtraArgs={"ContentType": content_type})

    def open_stream(self, key: str, chunk_size: int = 1024 * 1024) -> tuple[Iterator[bytes], int | None]:
        obj = self.client.get_object(Bucket=self.bucket, Key=key)
        body = obj["Body"]
        size = obj.get("ContentLength")

        def chunks() -> Iterator[bytes]:
            try:
                yield from body.iter_chunks(chunk_size)
            finally:
                body.close()

        return chunks(), size

    def get_bytes(self, key: str) -> bytes:
        obj = self.client.get_object(Bucket=self.bucket, Key=key)
        return obj["Body"].read()

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception:
            return False

    def delete_prefix(self, prefix: str) -> None:
        keys = self.list_keys(prefix)
        if not keys:
            return
        for start in range(0, len(keys), 1000):
            chunk = keys[start : start + 1000]
            self.client.delete_objects(
                Bucket=self.bucket,
                Delete={"Objects": [{"Key": key} for key in chunk]},
            )

    def list_keys(self, prefix: str) -> list[str]:
        keys: list[str] = []
        token = None
        while True:
            kwargs = {"Bucket": self.bucket, "Prefix": prefix}
            if token:
                kwargs["ContinuationToken"] = token
            page = self.client.list_objects_v2(**kwargs)
            keys.extend(item["Key"] for item in page.get("Contents", []))
            if not page.get("IsTruncated"):
                break
            token = page.get("NextContinuationToken")
        return sorted(keys)

    def presign_get(self, key: str, expires: int = 3600) -> str | None:
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=expires,
        )

    def materialize(self, key: str, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self.get_bytes(key))
