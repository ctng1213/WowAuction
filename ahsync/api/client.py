"""HTTP for AHF: retries, backoff, 429, gzip and secret hygiene (SPEC §2.1, §5.2).

All network access goes through an injected `transport` (a callable taking an
`HttpRequest` and returning an `HttpResponse`), so tests use FakeTransport.
The API key is sent only in the Authorization header. It and every capability
token are registered with a `Redactor`, which scrubs them from exception
messages and, through `RedactingFilter`, from the log.
"""
from __future__ import annotations

import gzip
import json
import logging
import urllib.error
import urllib.request
import zlib
from collections.abc import Callable
from dataclasses import dataclass, field

from ahsync.clock import Clock

log = logging.getLogger("ahsync.api")

MAX_RETRIES = 5          # SPEC §5.2: retry 5 times, then skip this run
MAX_BACKOFF_S = 60.0     # backoff 1, 2, 4 … capped at 60 s
MAX_RETRY_AFTER_S = 300.0   # a longer Retry-After skips the run (SPEC §5.2)
TIMEOUT_S = 60
USER_AGENT = "AHSync/0.1"


@dataclass(frozen=True)
class HttpRequest:
    """One GET request. `headers` may hold the Authorization header, so never log it."""

    url: str
    headers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class HttpResponse:
    """A raw response: status code, lower-cased header names, undecoded body bytes."""

    status: int
    headers: dict[str, str]
    body: bytes


Transport = Callable[[HttpRequest], HttpResponse]


class TransportError(Exception):
    """Network failure before any HTTP status (timeout, refused, DNS). Retried like a 5xx."""


class ApiError(Exception):
    """An AHF request failed for good. Messages never contain the key or a capability token."""


class NotFound(ApiError):
    """404: the file doesn't exist (SPEC §2.1: only request listed files)."""


class FeatureUnavailable(ApiError):
    """403 {feature}: our tier lacks it, so the feature is skipped (SPEC §2.1)."""

    def __init__(self, feature: str, message: str) -> None:
        """Create the error.

        Args:
            feature: The feature AHF named in the 403 body, or "unknown".
            message: Redacted description for the log.
        """
        super().__init__(message)
        self.feature = feature


class RetriesExhausted(ApiError):
    """5xx, 429 or network errors on every try: skip this run (SPEC §5.2, FM1)."""


class RateLimited(RetriesExhausted):
    """429 with a Retry-After longer than 300 s: skip this run instead of waiting (SPEC §5.2)."""


class Unauthorized(ApiError):
    """401 even without the key (or with no key to drop)."""


class BadFile(ApiError):
    """The body was truncated or isn't valid gzip/JSON (SPEC §5.2, FM2). Not committed."""


class Redactor:
    """Remembers secrets (API key, capability tokens) and removes them from text."""

    MASK = "***"

    def __init__(self) -> None:
        """Start with no secrets."""
        self._secrets: set[str] = set()

    def add(self, secret: str | None) -> None:
        """Register a secret. Strings shorter than 4 characters are ignored (too generic to scrub).

        Args:
            secret: The key or a capability token; None is ignored.
        """
        if secret and len(secret) >= 4:
            self._secrets.add(secret)

    def scrub(self, text: str) -> str:
        """Replace every registered secret in text with ***.

        Args:
            text: Any message, URL or log line.

        Returns:
            The text without secrets. Longer secrets are replaced first, so a
            token containing another token is fully masked.
        """
        for secret in sorted(self._secrets, key=lambda s: (-len(s), s)):
            text = text.replace(secret, self.MASK)
        return text


class RedactingFilter(logging.Filter):
    """Log filter that scrubs secrets from every record (SPEC §5.2: the log never holds the key)."""

    def __init__(self, redactor: Redactor) -> None:
        """Create the filter.

        Args:
            redactor: Holds the secrets to scrub.
        """
        super().__init__()
        self.redactor = redactor

    def filter(self, record: logging.LogRecord) -> bool:
        """Rewrite the record's message without secrets; never drops a record.

        Args:
            record: The log record; its msg/args are replaced by the scrubbed text.

        Returns:
            Always True.
        """
        message = self.redactor.scrub(record.getMessage())
        record.msg, record.args = message, None
        if record.exc_info:
            record.exc_text = self.redactor.scrub(logging.Formatter().formatException(record.exc_info))
            record.exc_info = None
        return True


def decode_body(body: bytes) -> object:
    """Decompress (when gzip) and parse a JSON body.

    Args:
        body: Raw response bytes; gzip is detected by its magic bytes.

    Returns:
        The decoded JSON value.

    Raises:
        BadFile: Truncated or corrupt gzip, or invalid JSON/UTF-8.
    """
    try:
        if body[:2] == b"\x1f\x8b":
            body = gzip.decompress(body)
        return json.loads(body)
    except (EOFError, OSError, zlib.error, ValueError) as error:
        raise BadFile(f"bad file: {type(error).__name__}") from None


def backoff_seconds(attempt: int) -> float:
    """Wait before retry number `attempt` (SPEC §5.2): 1, 2, 4, 8 … capped at 60 s.

    Args:
        attempt: 0 for the first retry.

    Returns:
        Seconds to wait.
    """
    return min(MAX_BACKOFF_S, float(2 ** attempt))


def retry_after_seconds(headers: dict[str, str], attempt: int) -> float:
    """How long a 429 asks us to wait.

    Args:
        headers: Response headers, lower-cased names.
        attempt: 0 for the first retry, used when Retry-After is missing.

    Returns:
        Retry-After in seconds when it is a number (the caller refuses more than
        300 s), else the backoff.
    """
    value = headers.get("retry-after", "").strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        return backoff_seconds(attempt)


class ApiClient:
    """GETs JSON from AHF with the retry rules of SPEC §5.2.

    Requests under `api_base` are rate limited to `per_minute`; CDN files are not.
    """

    def __init__(self, transport: Transport, clock: Clock, api_key: str = "",
                 api_base: str = "https://auctionhouse.fyi/api/v1", per_minute: int = 60,
                 redactor: Redactor | None = None) -> None:
        """Create a client.

        Args:
            transport: Performs one HTTP request.
            clock: For backoff waits and the rate limit.
            api_key: "" for no key. Sent only as `Authorization: Bearer`.
            api_base: API root; requests under it count toward the rate limit.
            per_minute: Allowed API requests per minute (60 without a key, from /me).
            redactor: Shared secret registry; one is created if omitted.
        """
        self._transport = transport
        self._clock = clock
        self._api_key = api_key
        self.api_base = api_base.rstrip("/")
        self.per_minute = per_minute
        self.redactor = redactor or Redactor()
        self.redactor.add(api_key)
        self._api_times: list[int] = []
        self.key_rejected = False    # set when a 401 made us drop the key for the rest of the run (FM25)
        self.key_used = False        # set when a keyed request succeeded

    @property
    def has_key(self) -> bool:
        """Whether requests still carry the key.

        Returns:
            True when a key is configured and hasn't been rejected this run.
        """
        return bool(self._api_key) and not self.key_rejected

    def _throttle(self, url: str) -> None:
        """Wait so API requests stay within `per_minute` over any 60 s.

        Args:
            url: The request URL; only API URLs are throttled.
        """
        if not url.startswith(self.api_base):
            return
        now = self._clock.now_ms()
        self._api_times = [t for t in self._api_times if now - t < 60_000]
        if len(self._api_times) >= self.per_minute:
            self._clock.sleep((self._api_times[0] + 60_000 - now) / 1000)
            now = self._clock.now_ms()
            self._api_times = [t for t in self._api_times if now - t < 60_000]
        self._api_times.append(now)

    def _send(self, url: str, auth: bool) -> HttpResponse | None:
        """Send one request.

        Args:
            url: Absolute URL. Never contains the key.
            auth: Attach the key, when there is one.

        Returns:
            The response, or None on a network error.
        """
        headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"}
        if auth and self.has_key:
            headers["Authorization"] = "Bearer " + self._api_key
        self._throttle(url)
        try:
            return self._transport(HttpRequest(url, headers))
        except TransportError as error:
            log.warning("network error for %s: %s", self.redactor.scrub(url), self.redactor.scrub(str(error)))
            return None

    def get_json(self, url: str, auth: bool = True) -> object:
        """GET and decode one JSON document, retrying transient failures.

        A 401 with the key is retried once without it. If that works, the key is
        dropped for the rest of the run and `key_rejected` is set (SPEC §5.2, FM25).

        Args:
            url: Absolute URL.
            auth: Attach the key, when there is one (CDN public files don't need it).

        Returns:
            The decoded JSON.

        Raises:
            NotFound: 404.
            FeatureUnavailable: 403; the body's `feature` names what's missing.
            Unauthorized: 401 without a key to drop, or again without the key.
            RateLimited: 429 asking for more than 300 s.
            RetriesExhausted: 5xx, 429 or network errors on the first try and all 5 retries.
            BadFile: truncated or invalid body (not retried within the run).
            ApiError: any other status.
        """
        keyed = auth and self.has_key
        try:
            data = self._get_with_retries(url, auth)
        except Unauthorized:
            if not keyed:
                raise
            data = self._get_with_retries(url, False)
            self.key_rejected = True
            log.warning("API key rejected (401); continuing without it on the free path")
            return data
        if keyed:
            self.key_used = True
        return data

    def _get_with_retries(self, url: str, auth: bool) -> object:
        """One GET with the retry rules, without the 401 fallback.

        Args:
            url: Absolute URL.
            auth: Attach the key, when there is one.

        Returns:
            The decoded JSON.

        Raises:
            As get_json.
        """
        shown = self.redactor.scrub(url)
        for attempt in range(MAX_RETRIES + 1):
            response = self._send(url, auth)
            if response is not None:
                self._raise_for_final_status(response, shown)
                if response.status == 200:
                    return decode_body(response.body)
            if attempt == MAX_RETRIES:
                break
            wait = self._wait_before_retry(response, attempt, shown)
            log.info("%s for %s; retry %d in %g s",
                     response.status if response is not None else "network error", shown, attempt + 1, wait)
            self._clock.sleep(wait)
        raise RetriesExhausted(f"gave up on {shown} after {MAX_RETRIES} retries")

    def _raise_for_final_status(self, response: HttpResponse, shown: str) -> None:
        """Raise for statuses that retrying won't fix.

        Args:
            response: The response.
            shown: The URL with secrets scrubbed, for messages.

        Raises:
            NotFound, FeatureUnavailable, Unauthorized, ApiError: As get_json.
        """
        status = response.status
        if status == 404:
            raise NotFound(f"404 for {shown}")
        if status == 403:
            feature = self._feature_of(response.body)
            raise FeatureUnavailable(feature, f"403 for {shown}: tier lacks {feature}")
        if status == 401:
            raise Unauthorized(f"401 for {shown}")
        if status not in (200, 429) and status < 500:
            raise ApiError(f"HTTP {status} for {shown}")

    def _wait_before_retry(self, response: HttpResponse | None, attempt: int, shown: str) -> float:
        """Seconds to wait before the next try.

        Args:
            response: The failed response, or None after a network error.
            attempt: 0 for the first retry.
            shown: The URL with secrets scrubbed, for messages.

        Returns:
            Retry-After for a 429, else the backoff.

        Raises:
            RateLimited: Retry-After above 300 s.
        """
        if response is None or response.status != 429:
            return backoff_seconds(attempt)
        wait = retry_after_seconds(response.headers, attempt)
        if wait > MAX_RETRY_AFTER_S:
            raise RateLimited(f"429 for {shown} asks for {wait:g} s; skipping this run")
        return wait

    def _feature_of(self, body: bytes) -> str:
        """Read the feature name from a 403 body like {"feature": "books"}.

        Args:
            body: The 403 response body.

        Returns:
            The feature name (redacted), or "unknown" if the body isn't the expected JSON.
        """
        try:
            data = decode_body(body)
        except BadFile:
            return "unknown"
        feature = data.get("feature") if isinstance(data, dict) else None
        return self.redactor.scrub(str(feature)) if feature else "unknown"


def build_request(request: HttpRequest) -> urllib.request.Request:
    """Turn an HttpRequest into a urllib Request that keeps the key off redirects.

    Authorization is added as an unredirected header, so urllib doesn't copy it
    onto a redirected request, which could go to another host.

    Args:
        request: URL and headers.

    Returns:
        The urllib request.
    """
    headers = {k: v for k, v in request.headers.items() if k.lower() != "authorization"}
    req = urllib.request.Request(request.url, headers=headers, method="GET")
    for name, value in request.headers.items():
        if name.lower() == "authorization":
            req.add_unredirected_header("Authorization", value)
    return req


class UrllibTransport:
    """The real transport, over urllib. Used only by `ahsync.__main__`."""

    def __init__(self, opener: Callable[..., object] = urllib.request.urlopen, timeout_s: float = TIMEOUT_S) -> None:
        """Create the transport.

        Args:
            opener: urllib.request.urlopen, or a stand-in with the same signature in tests.
            timeout_s: Socket timeout per request, in seconds.
        """
        self._opener = opener
        self._timeout_s = timeout_s

    def __call__(self, request: HttpRequest) -> HttpResponse:
        """Perform one GET.

        Args:
            request: URL and headers.

        Returns:
            The response, including non-200 statuses.

        Raises:
            TransportError: Timeout or connection failure. The message holds no headers.
        """
        req = build_request(request)
        try:
            with self._opener(req, timeout=self._timeout_s) as response:
                return HttpResponse(response.status, {k.lower(): v for k, v in response.headers.items()},
                                    response.read())
        except urllib.error.HTTPError as error:
            headers = {k.lower(): v for k, v in (error.headers or {}).items()}
            return HttpResponse(error.code, headers, error.read() or b"")
        except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
            raise TransportError(type(error).__name__) from None
