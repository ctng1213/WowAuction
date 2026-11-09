"""The AI assistant server's protocol, shared by the server and the bridge (SPEC §3.8.2, §3.8.3).

MCP is JSON-RPC 2.0. AHSync speaks the small subset an assistant needs: `initialize`, `ping`, `tools/list` and
`tools/call`, plus the `notifications/initialized` notification. Everything here is pure: messages in, messages out.
"""

from __future__ import annotations

import json

# The MCP versions AHSync answers to, newest first. The subset used is the same in each.
PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
SERVER_NAME = "AHSync"
# The dashboard pages get_page serves; the same as web.server.PAGES (a test checks).
PAGES = ("portfolio", "today", "week", "on_ah", "disenchant", "craft", "flip", "gathering", "inventory", "history",
         "rules", "characters", "status")

PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS = -32700, -32600, -32601, -32602

# What the assistant can't know, given once at `initialize` (SPEC §3.8.2: context, not instructions; MCP7).
INSTRUCTIONS = (
    "AHSync is a companion app for World of Warcraft: Forever, a Classic-era realm; this connector reads the user's "
    "own AHSync data. Item ids are Classic's. A random-suffix item's key is its item id + 2147483648 x the suffix id; "
    "otherwise the key is the item id. Money is integer copper (100 copper = 1 silver, 100 silver = 1 gold). Times "
    "are UTC Unix milliseconds. Every number comes from the last refresh, which list_markets dates."
)

_MARKET = {"type": "string", "description": "A market id from list_markets, e.g. forever.pvp.horde.us. "
                                            "Default: the first configured market."}

# The tools (SPEC §3.8.3). The server answers each; the bridge lists them without the app running.
TOOLS = (
    {"name": "list_markets",
     "description": "The user's markets: each one's id, when its numbers were last refreshed, its primary price "
                    "source and how many items it prices. Call this first to know how fresh an answer is.",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "get_page",
     "description": "One AHSync dashboard page as the dashboard shows it, with the engine's own numbers and its column "
                    "help texts: portfolio, today, week, on_ah, disenchant, craft, flip, gathering, inventory, "
                    "history, rules, characters or status. Long lists are cut to `limit`, with the full counts given.",
     "inputSchema": {"type": "object", "properties": {
         "page": {"type": "string", "enum": list(PAGES)},
         "market": _MARKET,
         "character": {"type": "string", "description": "A character id (as the page lists them) for the gathering "
                                                        "and inventory pages' per-character view."},
         "sort": {"type": "string", "description": "One of the page rows' sort keys; highest first."},
         "ascending": {"type": "boolean", "description": "Sort lowest first instead."},
         "limit": {"type": "integer", "minimum": 1, "maximum": 200, "description": "Rows per list, default 25."},
         "search": {"type": "string", "description": "Keep the rows whose item name contains this text."}},
         "required": ["page"], "additionalProperties": False}},
    {"name": "item",
     "description": "One item's detail as the dashboard shows it: market lines (price now and over 7, 14 and 30 days, "
                    "listings, chance to sell, best buy and sell times, disenchant value, vendor price), best selling "
                    "windows and market activity.",
     "inputSchema": {"type": "object", "properties": {
         "key": {"type": "integer", "description": "The item key (see the instructions)."},
         "name": {"type": "string", "description": "The item's exact name, when no key is given."},
         "market": _MARKET,
         "heatmaps": {"type": "boolean", "description": "Also return the hour-of-week heatmaps (large)."}},
         "additionalProperties": False}},
)


class ToolError(Exception):
    """A tool's failure, returned to the assistant as a result with isError (SPEC §3.8.3: errors are data, MCP13)."""

    def __init__(self, message: str) -> None:
        """Hold the plain sentence the assistant reads.

        Args:
            message: One sentence saying what was wrong and what is allowed.
        """
        super().__init__(message)
        self.message = message


def tool_names() -> tuple[str, ...]:
    """The tools' names, in listing order.

    Returns:
        Names.
    """
    return tuple(t["name"] for t in TOOLS)


def result(msg_id: object, value: dict) -> dict:
    """A JSON-RPC success response.

    Args:
        msg_id: The request's id.
        value: The result object.

    Returns:
        The response message.
    """
    return {"jsonrpc": "2.0", "id": msg_id, "result": value}


def error(msg_id: object, code: int, message: str) -> dict:
    """A JSON-RPC error response.

    Args:
        msg_id: The request's id (None when it couldn't be read).
        code: A JSON-RPC error code.
        message: A short explanation.

    Returns:
        The response message.
    """
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def tool_result(value: object) -> dict:
    """A tools/call result carrying data: JSON text for any client, and the same as structured content.

    Args:
        value: JSON-serializable data.

    Returns:
        The result object.
    """
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return {"content": [{"type": "text", "text": text}],
            "structuredContent": value if isinstance(value, dict) else {"value": value}, "isError": False}


def tool_error(message: str) -> dict:
    """A tools/call result for a failure the assistant should read (SPEC §3.8.3, MCP6, MCP13).

    Args:
        message: One plain sentence.

    Returns:
        The result object.
    """
    return {"content": [{"type": "text", "text": message}], "isError": True}


def initialize_result(requested: object, version: str) -> dict:
    """The answer to `initialize` (MCP5, MCP7): the version, the tools capability and the context.

    Args:
        requested: The protocolVersion the client asked for.
        version: AHSync's version, for serverInfo.

    Returns:
        The result object; the requested version when AHSync speaks it, else the newest it does.
    """
    chosen = requested if requested in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
    return {"protocolVersion": chosen, "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": version}, "instructions": INSTRUCTIONS}


def parse_message(text: str) -> tuple[dict | None, dict | None]:
    """Read one JSON-RPC message.

    Args:
        text: One line or request body.

    Returns:
        (message, None) for a well-formed request or notification; (None, error response) otherwise.
    """
    try:
        msg = json.loads(text)
    except (ValueError, RecursionError):
        return None, error(None, PARSE_ERROR, "Parse error")
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
        return None, error(msg.get("id") if isinstance(msg, dict) else None, INVALID_REQUEST, "Invalid request")
    return msg, None


def is_notification(msg: dict) -> bool:
    """Whether a message expects no response (it has no id).

    Args:
        msg: A parsed message.

    Returns:
        True for a notification.
    """
    return "id" not in msg


def local_answer(msg: dict, version: str) -> dict | None:
    """The answer to a method that needs no data: initialize, ping, tools/list (SPEC §3.8.2: the bridge answers these).

    Args:
        msg: A parsed request.
        version: AHSync's version.

    Returns:
        The response; None for tools/call (it needs the server) or a notification.
    """
    if is_notification(msg):
        return None
    method, msg_id = msg["method"], msg.get("id")
    params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
    if method == "initialize":
        return result(msg_id, initialize_result(params.get("protocolVersion"), version))
    if method == "ping":
        return result(msg_id, {})
    if method == "tools/list":
        return result(msg_id, {"tools": list(TOOLS)})
    if method == "tools/call":
        return None
    return error(msg_id, METHOD_NOT_FOUND, f"Method not found: {method}")
