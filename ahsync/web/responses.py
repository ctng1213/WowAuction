"""The dashboard's HTTP responses and its one body reader, shared by the server and its endpoint mixins."""

from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass(frozen=True)
class Response:
    """One HTTP response, built by DashboardApp.handle and written by the handler."""

    status: int
    body: bytes
    content_type: str = "application/json; charset=utf-8"
    headers: tuple[tuple[str, str], ...] = ()


def json_response(status: int, payload: object, headers: tuple[tuple[str, str], ...] = ()) -> Response:
    """Build a JSON response.

    Args:
        status: HTTP status code.
        payload: JSON-serializable body.
        headers: Extra headers, e.g. (("Allow", "POST"),).

    Returns:
        The response with a compact UTF-8 JSON body.
    """
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return Response(status, body, headers=headers)


def body_field(body: bytes, name: str) -> object:
    """One field of a JSON object body.

    Args:
        body: The request body.
        name: The field.

    Returns:
        Its value; None when the body isn't a JSON object or lacks it.
    """
    try:
        data = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    return data.get(name) if isinstance(data, dict) else None
