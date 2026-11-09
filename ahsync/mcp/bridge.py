"""The bridge Claude launches (SPEC §3.8.2): MCP over stdin/stdout, relayed to the server inside AHSync.

It answers `initialize`, `ping` and `tools/list` itself, so Claude connects whether or not AHSync runs, and forwards
each `tools/call` to `127.0.0.1:<port>/mcp` with the key read from `mcp.json` at that call. It holds no data, opens no
listener, and stops when Claude closes stdin. Standard library only, so it starts in milliseconds.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import TextIO

from ahsync.mcp import protocol

KEY_FILE = "mcp.json"                  # the server's: {"port", "key"} while enabled
CALL_TIMEOUT_S = 60
OFF = ("AHSync's AI assistant access is off, or AHSync isn't running. Start AHSync and press Enable on its Status "
       "page (AI assistant), then ask again.")
NOT_RUNNING = "AHSync isn't running. Start it and ask again."
TOO_SLOW = "AHSync took too long to answer. Ask again in a moment."

# (url, body, headers, timeout s) -> (HTTP status, response body); raises OSError when nothing listens.
Post = Callable[[str, bytes, dict, float], tuple[int, bytes]]


def http_post(url: str, body: bytes, headers: dict, timeout_s: float) -> tuple[int, bytes]:
    """POST to the server on 127.0.0.1, without any proxy.

    Args:
        url: http://127.0.0.1:<port>/mcp.
        body: The JSON-RPC request.
        headers: Authorization and Content-Type.
        timeout_s: Seconds to wait.

    Returns:
        (status, body); an HTTP error status is returned, not raised.

    Raises:
        OSError: Nothing answers (connection refused), or the wait timed out.
    """
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with opener.open(request, timeout=timeout_s) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def read_key_file(data_dir: Path) -> tuple[int, str] | None:
    """The server's port and key.

    Args:
        data_dir: AHSync's data folder.

    Returns:
        (port, key); None when mcp.json is missing or unreadable (access off, or AHSync never enabled it).
    """
    try:
        found = json.loads((data_dir / KEY_FILE).read_text(encoding="utf-8"))
        port, key = found["port"], found["key"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if isinstance(port, bool) or not isinstance(port, int) or not isinstance(key, str):
        return None
    return port, key


def forward(msg: dict, data_dir: Path, post: Post) -> dict:
    """A tools/call answered by the server, or the reason it can't be (MCP5, MCP6).

    Args:
        msg: A tools/call request.
        data_dir: AHSync's data folder.
        post: How to POST (http_post; a fake in tests).

    Returns:
        The JSON-RPC response.
    """
    msg_id = msg.get("id")
    found = read_key_file(data_dir)
    if found is None:
        return protocol.result(msg_id, protocol.tool_error(OFF))
    port, key = found
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    try:
        status, body = post(f"http://127.0.0.1:{port}/mcp", json.dumps(msg).encode("utf-8"), headers, CALL_TIMEOUT_S)
    except TimeoutError:
        return protocol.result(msg_id, protocol.tool_error(TOO_SLOW))
    except OSError:
        return protocol.result(msg_id, protocol.tool_error(NOT_RUNNING))
    if status != 200:                      # a key from an older start, or another program on the port
        return protocol.result(msg_id, protocol.tool_error(NOT_RUNNING))
    try:
        return json.loads(body)
    except ValueError:
        return protocol.result(msg_id, protocol.tool_error(NOT_RUNNING))


def respond(line: str, data_dir: Path, version: str, post: Post) -> dict | None:
    """The answer to one line from Claude.

    Args:
        line: One JSON-RPC message.
        data_dir: AHSync's data folder.
        version: AHSync's version.
        post: How to POST.

    Returns:
        The response; None for a notification or a blank line.
    """
    if not line.strip():
        return None
    msg, bad = protocol.parse_message(line)
    if bad is not None:
        return bad
    if protocol.is_notification(msg):
        return None
    local = protocol.local_answer(msg, version)
    return local if local is not None else forward(msg, data_dir, post)


def run(lines: Iterable[str], out: TextIO, data_dir: Path, version: str, post: Post = http_post) -> None:
    """Serve Claude until stdin closes: one response line per request, in order.

    Args:
        lines: stdin's lines.
        out: stdout.
        data_dir: AHSync's data folder.
        version: AHSync's version.
        post: How to POST.
    """
    for line in lines:
        answer = respond(line, data_dir, version, post)
        if answer is not None:
            out.write(json.dumps(answer, ensure_ascii=False, separators=(",", ":")) + "\n")
            out.flush()
