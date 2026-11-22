"""The one place the cloud job talks to AWS through boto3 (SPEC §2.4, AR9).

`S3Store` is the Store protocol over a bucket; `get_secret` reads a Secrets Manager value; `metric` writes one
CloudWatch metric as an Embedded Metric Format log line (no API call, no permission beyond the logs);
`alarm_states` and `metric_sums` read CloudWatch for the status document (SPEC §2.4 The status page, SEC21).
boto3 is imported lazily, inside the functions that need it, so the rest of ahcloud and its tests run without
it; tests hand `S3Store` a fake client.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ahcloud.store import CHUNK_BYTES, PreconditionFailed, StoredObject, check_key

NAMESPACE = "WowAuction"
# The status's CloudWatch reads are bounded: they run at the end of a run that may be near its timeout (FM81).
CLOUDWATCH_TIMEOUTS = {"connect_timeout": 5, "read_timeout": 10, "retries": {"max_attempts": 2}}
# A file is uploaded in parts of this size (S3: 5 MiB to 5 GiB a part, at most 10,000 parts), so a 9 GB history is
# 135 parts and only one is in memory at a time (FM91); a file no larger goes as one put.
PART_BYTES = 64 * 1024 * 1024
# The multipart upload is declared CRC32 at its creation, each part is sent with it and the completion names each
# part's: S3 checks every part end to end, and newer SDKs (which add a CRC32 to parts on their own) and older ones
# behave alike. A completion naming checksums of an upload created without one is refused (InvalidPart, measured
# 2026-11-22 against the data bucket).
CHECKSUM = "CRC32"

log = logging.getLogger(__name__)


def _precondition(error: Exception) -> bool:
    """Whether an S3 error is a lost conditional write.

    Args:
        error: A botocore ClientError.

    Returns:
        True for 412 PreconditionFailed, and for 404 NoSuchKey (a conditional write to a missing key).
    """
    code = str(getattr(error, "response", {}).get("Error", {}).get("Code", ""))
    return code in ("PreconditionFailed", "412", "NoSuchKey", "404")


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

    def put(self, key: str, data: bytes, if_match: str | None = None, *, cache_control: str | None = None,
            content_type: str | None = None) -> str:
        """Write an object; a conditional write uses S3's If-Match (see Store.put).

        Args:
            key: The object's key.
            data: Its bytes.
            if_match: The ETag it must still have, or None.
            cache_control: The object's Cache-Control, which CloudFront honours (the status: "max-age=60"); None
                for none.
            content_type: The object's Content-Type; None for S3's default.

        Returns:
            The new ETag.

        Raises:
            PreconditionFailed: S3 answered 412 (or 404 for a conditional write to a missing key).
        """
        extra = {name: value for name, value in (("IfMatch", if_match), ("CacheControl", cache_control),
                                                 ("ContentType", content_type)) if value is not None}
        try:
            answer = self._s3.put_object(Bucket=self.bucket, Key=check_key(key), Body=data, **extra)
        except self._s3.exceptions.ClientError as error:
            if _precondition(error):
                raise PreconditionFailed(key) from None
            raise
        return answer["ETag"]

    def etag(self, key: str) -> str | None:
        """An object's ETag from its headers (see Store.etag).

        Args:
            key: The object's key.

        Returns:
            Its ETag, or None when there is no such key.
        """
        try:
            return self._s3.head_object(Bucket=self.bucket, Key=check_key(key))["ETag"]
        except self._s3.exceptions.ClientError as error:
            if str(error.response.get("Error", {}).get("Code", "")) in ("404", "NoSuchKey", "NotFound"):
                return None
            raise

    def get_file(self, key: str, path: Path) -> str | None:
        """Stream an object into a file through a temporary file and a rename (see Store.get_file).

        Args:
            key: The object's key.
            path: The file to write.

        Returns:
            Its ETag, or None when there is no such key.
        """
        try:
            answer = self._s3.get_object(Bucket=self.bucket, Key=check_key(key))
        except self._s3.exceptions.NoSuchKey:
            return None
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, temp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
        try:
            with os.fdopen(handle, "wb") as out:
                while chunk := answer["Body"].read(CHUNK_BYTES):
                    out.write(chunk)
            os.replace(temp, path)
        except BaseException:
            Path(temp).unlink(missing_ok=True)
            raise
        return answer["ETag"]

    def put_file(self, key: str, path: Path, if_match: str | None = None) -> str:
        """Stream a file up: one put when it fits a part, else a multipart upload completed with S3's If-Match
        (see Store.put_file). A failed multipart upload is aborted, so no parts are left behind (FM91).

        Args:
            key: The object's key.
            path: The file.
            if_match: The ETag it must still have, or None.

        Returns:
            The new ETag.

        Raises:
            PreconditionFailed: S3 answered 412 (or 404 for a conditional write to a missing key).
        """
        check_key(key)
        condition = {} if if_match is None else {"IfMatch": if_match}
        if path.stat().st_size <= PART_BYTES:
            with path.open("rb") as body:
                try:
                    return self._s3.put_object(Bucket=self.bucket, Key=key, Body=body, **condition)["ETag"]
                except self._s3.exceptions.ClientError as error:
                    if _precondition(error):
                        raise PreconditionFailed(key) from None
                    raise
        upload = self._s3.create_multipart_upload(Bucket=self.bucket, Key=key, ChecksumAlgorithm=CHECKSUM)["UploadId"]
        try:
            parts = []
            with path.open("rb") as source:
                while chunk := source.read(PART_BYTES):
                    number = len(parts) + 1
                    answer = self._s3.upload_part(Bucket=self.bucket, Key=key, UploadId=upload, PartNumber=number,
                                                  Body=chunk, ChecksumAlgorithm=CHECKSUM)
                    parts.append({"PartNumber": number, "ETag": answer["ETag"],
                                  "ChecksumCRC32": answer["ChecksumCRC32"]})
            return self._s3.complete_multipart_upload(Bucket=self.bucket, Key=key, UploadId=upload,
                                                      MultipartUpload={"Parts": parts}, **condition)["ETag"]
        except BaseException as error:
            self._abort(key, upload)
            if isinstance(error, self._s3.exceptions.ClientError) and _precondition(error):
                raise PreconditionFailed(key) from None
            raise

    def _abort(self, key: str, upload: str) -> None:
        """Abort a multipart upload, never hiding the error that caused it (a lost race must still read as one, FM83).

        Args:
            key: The object's key.
            upload: The upload's id.

        Side effects:
            A failed abort is logged and left to the bucket's lifecycle rule, which removes abandoned parts after a
            day (FM91).
        """
        try:
            self._s3.abort_multipart_upload(Bucket=self.bucket, Key=key, UploadId=upload)
        except Exception as error:     # noqa: BLE001  the original error is what the caller must see
            log.warning("abort of the upload of %s failed (%s); the lifecycle rule removes its parts", key,
                        type(error).__name__)

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


def cloudwatch_client() -> Any:
    """A boto3 CloudWatch client with short timeouts and few retries (CLOUDWATCH_TIMEOUTS).

    Returns:
        The client.
    """
    import boto3   # noqa: PLC0415  lazy
    from botocore.config import Config   # noqa: PLC0415
    return boto3.client("cloudwatch", config=Config(**CLOUDWATCH_TIMEOUTS))


def alarm_states(names: list[str], client: Any = None) -> list[tuple[str, str, int]]:
    """The stack's metric alarms, asked for by name, with their states (SEC21).

    By name, never by prefix: the role may describe only `alarm:<stack>-*`, and a prefix request is checked against
    `alarm:*` and refused (LESSONS 2026-11-12).

    Args:
        names: The alarms' full names, e.g. "WowAuctionMarket-CollectorErrors" (at most 100).
        client: A boto3 cloudwatch client; None makes one.

    Returns:
        (name, state, when it entered that state in UTC ms) per alarm found, sorted by name; a name with no alarm
        is left out.
    """
    if client is None:
        client = cloudwatch_client()
    pages = client.get_paginator("describe_alarms").paginate(AlarmNames=list(names), AlarmTypes=["MetricAlarm"])
    # StateTransitionedTimestamp is when the state last changed; StateUpdatedTimestamp also moves on other updates
    return sorted((a["AlarmName"], a["StateValue"],
                   int((a.get("StateTransitionedTimestamp") or a["StateUpdatedTimestamp"]).timestamp() * 1000))
                  for page in pages for a in page.get("MetricAlarms", []))


def metric_sums(queries: dict[str, tuple[str, str, dict[str, str]]], start_ms: int, end_ms: int,
                client: Any = None) -> dict[str, int]:
    """Each metric's sum over a window, through GetMetricData (hourly sums added up; SEC21).

    Args:
        queries: Query id (lower-case letters) -> (namespace, metric name, dimension name -> value).
        start_ms: The window's start, UTC ms.
        end_ms: Its end, UTC ms.
        client: A boto3 cloudwatch client; None makes one.

    Returns:
        Query id -> the sum, a whole count (0 when the metric has no data in the window).
    """
    if client is None:
        client = cloudwatch_client()
    asked = [{"Id": qid, "ReturnData": True, "MetricStat": {
        "Metric": {"Namespace": namespace, "MetricName": name,
                   "Dimensions": [{"Name": k, "Value": v} for k, v in sorted(dims.items())]},
        "Period": 3600, "Stat": "Sum"}} for qid, (namespace, name, dims) in sorted(queries.items())]
    sums = {qid: 0.0 for qid in queries}
    token: str | None = None
    while True:
        answer = client.get_metric_data(
            MetricDataQueries=asked, StartTime=datetime.fromtimestamp(start_ms / 1000, timezone.utc),
            EndTime=datetime.fromtimestamp(end_ms / 1000, timezone.utc), **({"NextToken": token} if token else {}))
        for result in answer.get("MetricDataResults", []):
            sums[result["Id"]] += sum(result.get("Values", []))
        token = answer.get("NextToken")
        if not token:
            return {qid: int(total) for qid, total in sums.items()}
