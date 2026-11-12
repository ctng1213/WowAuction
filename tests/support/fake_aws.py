"""Fakes for the cloud job's AWS edge (SPEC §8.1: no network in tests): an S3 client and a secrets client.

`FakeS3Client` answers the calls `ahcloud.aws.S3Store` makes (get_object, put_object with IfMatch,
list_objects_v2 through a paginator) from a dict, with ETags as S3 quotes them and pages of `page_size` keys,
so the real store class is tested, not a stand-in. `FakeSecrets` answers get_secret_value. `FakeCloudWatch` answers
describe_alarms (through a paginator) and get_metric_data (in pages, with NextToken).
"""
from __future__ import annotations

import hashlib
import io


class ClientError(Exception):
    """botocore's ClientError shape: `.response["Error"]["Code"]`."""

    def __init__(self, code: str, operation: str = "op") -> None:
        super().__init__(f"An error occurred ({code}) when calling the {operation} operation")
        self.response = {"Error": {"Code": code}}


class NoSuchKey(ClientError):
    """S3's NoSuchKey, a ClientError subclass as in boto3."""

    def __init__(self) -> None:
        super().__init__("NoSuchKey", "GetObject")


class _Exceptions:
    NoSuchKey = NoSuchKey
    ClientError = ClientError


class _Paginator:
    def __init__(self, client: "FakeS3Client") -> None:
        self._client = client

    def paginate(self, Bucket: str, Prefix: str = "", StartAfter: str = ""):    # noqa: N803  boto3's names
        keys = sorted(k for k in self._client.objects if k.startswith(Prefix) and k > StartAfter)
        size = self._client.page_size
        for start in range(0, max(len(keys), 1), size):
            page = keys[start:start + size]
            self._client.calls.append(("list_page", Bucket, len(page)))
            yield {"Contents": [{"Key": k} for k in page]} if page else {}


class FakeS3Client:
    """An in-memory bucket behind boto3's S3 client calls."""

    exceptions = _Exceptions

    def __init__(self, page_size: int = 1000) -> None:
        self.objects: dict[str, bytes] = {}
        self.headers: dict[str, dict[str, str]] = {}               # key -> CacheControl / ContentType of the last put
        self.page_size = page_size
        self.calls: list[tuple] = []

    @staticmethod
    def etag(data: bytes) -> str:
        return '"' + hashlib.md5(data).hexdigest() + '"'          # noqa: S324  S3's ETag shape, not security

    def get_object(self, Bucket: str, Key: str) -> dict:          # noqa: N803
        self.calls.append(("get", Bucket, Key))
        if Key not in self.objects:
            raise NoSuchKey()
        data = self.objects[Key]
        return {"Body": io.BytesIO(data), "ETag": self.etag(data)}

    def put_object(self, Bucket: str, Key: str, Body: bytes, IfMatch: str | None = None,   # noqa: N803
                   **headers: str) -> dict:
        self.calls.append(("put", Bucket, Key, IfMatch))
        self.headers[Key] = headers
        if IfMatch is not None:
            if Key not in self.objects:
                raise ClientError("NoSuchKey", "PutObject")
            if self.etag(self.objects[Key]) != IfMatch:
                raise ClientError("PreconditionFailed", "PutObject")
        self.objects[Key] = bytes(Body)
        return {"ETag": self.etag(self.objects[Key])}

    def delete_object(self, Bucket: str, Key: str) -> dict:     # noqa: N803
        self.calls.append(("delete", Bucket, Key))
        self.objects.pop(Key, None)
        return {}

    def get_paginator(self, name: str) -> _Paginator:
        assert name == "list_objects_v2"
        return _Paginator(self)


class FakeSecrets:
    """boto3's secretsmanager client, for get_secret_value only."""

    def __init__(self, values: dict[str, str] | None = None, denied: bool = False) -> None:
        self.values = values or {}
        self.denied = denied
        self.calls: list[str] = []

    def get_secret_value(self, SecretId: str) -> dict:           # noqa: N803
        self.calls.append(SecretId)
        if self.denied:
            raise ClientError("AccessDeniedException", "GetSecretValue")
        return {"SecretString": self.values[SecretId]}


class _AlarmPages:
    def __init__(self, client: "FakeCloudWatch") -> None:
        self._client = client

    def paginate(self, AlarmNames: list | None = None, AlarmNamePrefix: str = "",          # noqa: N803  boto3's names
                 AlarmTypes: list | None = None):
        """DescribeAlarms' pages, as the analyzer's role sees them: a request by name prefix is checked against
        `alarm:*` and refused under its grant on `alarm:<stack>-*`; a request by names passes (measured 2026-11-12)."""
        if AlarmNamePrefix or not AlarmNames:
            raise ClientError("AccessDenied", "DescribeAlarms")
        self._client.calls.append(("describe_alarms", tuple(AlarmNames), tuple(AlarmTypes or ())))
        alarms = [a for a in self._client.alarms if a["AlarmName"] in AlarmNames]
        for start in range(0, len(alarms), 2):                         # pages of two, to test the paging
            yield {"MetricAlarms": alarms[start:start + 2]}


class FakeCloudWatch:
    """CloudWatch's describe_alarms and get_metric_data over lists the test fills."""

    def __init__(self, alarms: list[dict] | None = None, values: dict[str, list[list[float]]] | None = None) -> None:
        self.alarms = alarms or []                  # DescribeAlarms' MetricAlarms entries
        self.values = values or {}                  # query id -> one list of values per page
        self.calls: list[tuple] = []

    def get_paginator(self, name: str) -> _AlarmPages:
        assert name == "describe_alarms"
        return _AlarmPages(self)

    def get_metric_data(self, MetricDataQueries: list, StartTime, EndTime, NextToken: str | None = None) -> dict:  # noqa: N803
        self.calls.append(("get_metric_data", tuple(q["Id"] for q in MetricDataQueries), StartTime, EndTime, NextToken))
        page = int(NextToken or 0)
        pages = max((len(v) for v in self.values.values()), default=1)
        results = [{"Id": q["Id"], "Values": self.values.get(q["Id"], [[]] * pages)[page]
                    if page < len(self.values.get(q["Id"], [])) else []} for q in MetricDataQueries]
        return {"MetricDataResults": results, **({"NextToken": str(page + 1)} if page + 1 < pages else {})}
