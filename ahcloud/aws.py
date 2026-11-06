"""The one place the cloud job talks to AWS through boto3 (SPEC §2.4, AR9).

`S3Store` is the Store protocol over a bucket; `get_secret` reads a Secrets Manager value; `metric` writes one
CloudWatch metric as an Embedded Metric Format log line (no API call, no permission beyond the logs).
boto3 is imported lazily, inside the functions that need it, so the rest of ahcloud and its tests run without
it; tests hand `S3Store` a fake client.
"""
from __future__ import annotations

import json
from typing import Any, Callable

from ahcloud.store import PreconditionFailed, StoredObject, check_key

NAMESPACE = "WowAuction"


class S3Store:
    """A Store over one S3 bucket."""

    def __init__(self, bucket: str, client: Any = None) -> None:
        """Use a bucket.

        Args:
            bucket: Bucket name.
            client: A boto3 S3 client; None makes one (needs boto3 and AWS credentials).
        """
        if client is None:
            import boto3   # noqa: PLC0415  lazy: only the real store needs it
            client = boto3.client("s3")
        self.bucket, self._s3 = bucket, client

    def get(self, key: str) -> StoredObject | None:
        """Read an object (see Store.get).

        Args:
            key: The object's key.

        Returns:
            Its bytes and ETag, or None when there is no such key.
        """
        try:
            answer = self._s3.get_object(Bucket=self.bucket, Key=check_key(key))
        except self._s3.exceptions.NoSuchKey:
            return None
        return StoredObject(answer["Body"].read(), answer["ETag"])

    def put(self, key: str, data: bytes, if_match: str | None = None) -> str:
        """Write an object; a conditional write uses S3's If-Match (see Store.put).

        Args:
            key: The object's key.
            data: Its bytes.
            if_match: The ETag it must still have, or None.

        Returns:
            The new ETag.

        Raises:
            PreconditionFailed: S3 answered 412 (or 404 for a conditional write to a missing key).
        """
        extra = {"IfMatch": if_match} if if_match is not None else {}
        try:
            answer = self._s3.put_object(Bucket=self.bucket, Key=check_key(key), Body=data, **extra)
        except self._s3.exceptions.ClientError as error:
            code = str(error.response.get("Error", {}).get("Code", ""))
            if code in ("PreconditionFailed", "412", "NoSuchKey", "404"):
                raise PreconditionFailed(key) from None
            raise
        return answer["ETag"]

    def delete(self, key: str) -> None:
        """Delete an object (see Store.delete; S3 treats a missing key as deleted).

        Args:
            key: The object's key.
        """
        self._s3.delete_object(Bucket=self.bucket, Key=check_key(key))

    def list(self, prefix: str, start_after: str = "") -> list[str]:
        """The keys under a prefix, every page (see Store.list).

        Args:
            prefix: Key prefix.
            start_after: Only keys sorting after this one.

        Returns:
            Keys in ascending order.
        """
        pages = self._s3.get_paginator("list_objects_v2").paginate(
            Bucket=self.bucket, Prefix=prefix, **({"StartAfter": start_after} if start_after else {}))
        return sorted(item["Key"] for page in pages for item in page.get("Contents", []))


def get_secret(name: str, client: Any = None) -> str:
    """A Secrets Manager secret's string value. Never log what this returns.

    Args:
        name: The secret's name or ARN.
        client: A boto3 secretsmanager client; None makes one.

    Returns:
        The SecretString.
    """
    if client is None:
        import boto3   # noqa: PLC0415  lazy
        client = boto3.client("secretsmanager")
    return client.get_secret_value(SecretId=name)["SecretString"]


def metric(name: str, value: float, dimensions: dict[str, str], now_ms: int,
           write: Callable[[str], None] = print) -> None:
    """One CloudWatch metric as an Embedded Metric Format log line (the alarms read these, SPEC §2.4).

    Args:
        name: Metric name, e.g. "snapshot_written".
        value: Its value (a count).
        dimensions: Dimension name -> value, e.g. {"market": "forever.normal.horde.us"}.
        now_ms: The time, UTC ms.
        write: Writes the line (stdout in Lambda goes to CloudWatch Logs).
    """
    write(json.dumps({"_aws": {"Timestamp": now_ms, "CloudWatchMetrics": [
        {"Namespace": NAMESPACE, "Dimensions": [sorted(dimensions)], "Metrics": [{"Name": name, "Unit": "Count"}]}]},
        name: value, **dimensions}, sort_keys=True))
