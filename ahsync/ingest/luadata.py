"""Parse the Lua subset WoW writes to SavedVariables files (SPEC §2.2).

``NAME = value`` statements whose values are strings (with escapes), numbers
(integers, floats, negatives, hex, exponents), booleans, nil and nested tables
with ``["k"] =``, ``[n] =``, ``name =`` and implicit array fields, plus ``--``
line and ``--[[ ]]`` block comments. Tables become dicts; array items are keyed
1..n, and nil fields are left out as in Lua.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Union

LuaValue = Union[str, int, float, bool, "LuaTable"]
LuaTable = dict  # Lua table: keys are str, int, float or bool; array items have keys 1..n


# ---------------------------------------------------------------- errors

class SavedVarsError(Exception):
    """The SavedVariables file can't be used at all (SPEC §5.2)."""


class SavedVarsParseError(SavedVarsError):
    """The file is not valid SavedVariables Lua, e.g. half-written.

    Attributes:
        line: 1-based line of the problem.
        column: 1-based column of the problem.
    """

    def __init__(self, message: str, line: int, column: int) -> None:
        """Build the error with its position.

        Args:
            message: What is wrong.
            line: 1-based line number.
            column: 1-based column number.
        """
        super().__init__(f"line {line}, column {column}: {message}")
        self.line = line
        self.column = column


# ---------------------------------------------------------------- Lua parser

_TOKEN = re.compile(
    r"""
    (?P<space>\s+)
  | (?P<comment>--(?:\[(?P<eq>=*)\[.*?\](?P=eq)\]|[^\n]*))
  | (?P<string>"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*')
  | (?P<number>0[xX][0-9a-fA-F]+|(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)
  | (?P<name>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<punct>[{}\[\]=,;-])
    """,
    re.VERBOSE | re.DOTALL,
)
_SIMPLE_ESCAPES = {"n": 10, "t": 9, "r": 13, "a": 7, "b": 8, "f": 12, "v": 11,
                   "\\": 92, '"': 34, "'": 39, "\n": 10}
_KEYWORDS = {"true": True, "false": False}


@dataclass(frozen=True, slots=True)
class _Token:
    """One lexical token: kind ("string", "number", "name", "punct", "end"), its text and offset."""

    kind: str
    text: str
    pos: int


def _position(text: str, pos: int) -> tuple[int, int]:
    """Turn a character offset into a line and column.

    Args:
        text: The whole source.
        pos: 0-based character offset.

    Returns:
        (1-based line, 1-based column).
    """
    line = text.count("\n", 0, pos) + 1
    return line, pos - (text.rfind("\n", 0, pos) + 1) + 1


def _tokenize(text: str) -> list[_Token]:
    """Split Lua source into tokens, dropping whitespace and comments.

    Args:
        text: The SavedVariables source.

    Returns:
        Tokens in order, ending with an "end" token.

    Raises:
        SavedVarsParseError: On a character that starts no token (e.g. an unterminated string).
    """
    tokens, pos = [], 0
    while pos < len(text):
        match = _TOKEN.match(text, pos)
        if not match:
            raise SavedVarsParseError(f"unexpected {text[pos]!r}", *_position(text, pos))
        kind = match.lastgroup if match.lastgroup != "eq" else "comment"
        if kind not in ("space", "comment"):
            tokens.append(_Token(kind, match.group(), pos))
        pos = match.end()
    tokens.append(_Token("end", "", pos))
    return tokens


def _decode_string(literal: str) -> str:
    """Decode a quoted Lua string literal, escapes included.

    Escapes produce bytes (``\\ddd`` is a byte), so the result is decoded as
    UTF-8, the encoding WoW writes; invalid sequences become U+FFFD.

    Args:
        literal: The literal with its quotes, e.g. '"Amaya\\n"'.

    Returns:
        The string value.

    Raises:
        ValueError: On an unknown escape or a \\ddd above 255.
    """
    body, out, i = literal[1:-1], bytearray(), 0
    while i < len(body):
        slash = body.find("\\", i)
        if slash < 0:
            out += body[i:].encode("utf-8", "surrogateescape")
            break
        out += body[i:slash].encode("utf-8", "surrogateescape")
        escape = body[slash + 1]
        if escape.isdigit():
            digits = re.match(r"\d{1,3}", body[slash + 1:]).group()
            if int(digits) > 255:
                raise ValueError(f"escape \\{digits} is above 255")
            out.append(int(digits))
            i = slash + 1 + len(digits)
        elif escape in _SIMPLE_ESCAPES:
            out.append(_SIMPLE_ESCAPES[escape])
            i = slash + 2
        else:
            raise ValueError(f"unknown escape \\{escape}")
    return out.decode("utf-8", "replace")


class _Parser:
    """Recursive-descent parser over the token list of one file."""

    def __init__(self, text: str) -> None:
        """Tokenize the source.

        Args:
            text: The SavedVariables source.
        """
        self.text = text
        self.tokens = _tokenize(text)
        self.index = 0

    def error(self, message: str, token: _Token | None = None) -> SavedVarsParseError:
        """Build a parse error at a token.

        Args:
            message: What is wrong.
            token: Where; default the current token.

        Returns:
            The error, for the caller to raise.
        """
        token = token or self.peek()
        found = token.text or "end of file"
        return SavedVarsParseError(f"{message} (found {found!r})", *_position(self.text, token.pos))

    def peek(self, ahead: int = 0) -> _Token:
        """Look at a token without consuming it.

        Args:
            ahead: 0 for the current token, 1 for the next.

        Returns:
            The token (the "end" token past the end).
        """
        return self.tokens[min(self.index + ahead, len(self.tokens) - 1)]

    def take(self) -> _Token:
        """Consume the current token.

        Returns:
            The consumed token.
        """
        token = self.peek()
        self.index += 1
        return token

    def expect(self, text: str) -> None:
        """Consume a punctuation token that must be there.

        Args:
            text: The expected punctuation, e.g. "=".

        Raises:
            SavedVarsParseError: When the current token is something else.
        """
        if self.peek().kind != "punct" or self.peek().text != text:
            raise self.error(f"expected {text!r}")
        self.take()

    def assignments(self) -> dict[str, LuaValue | None]:
        """Parse the whole file: ``NAME = value`` statements.

        Returns:
            Global name -> value (None for ``NAME = nil``); a later assignment wins.
        """
        result: dict[str, LuaValue | None] = {}
        while self.peek().kind != "end":
            name = self.take()
            if name.kind != "name" or name.text in _KEYWORDS or name.text == "nil":
                raise self.error("expected a global name", name)
            self.expect("=")
            result[name.text] = self.value()
            if self.peek().text == ";" and self.peek().kind == "punct":
                self.take()
        return result

    def value(self) -> LuaValue | None:
        """Parse one value.

        Returns:
            str, int, float, bool, a LuaTable dict, or None for nil.
        """
        token = self.take()
        if token.kind == "string":
            try:
                return _decode_string(token.text)
            except ValueError as exc:
                raise self.error(str(exc), token) from None
        if token.kind == "number":
            return _number(token.text)
        if token.kind == "punct" and token.text == "-" and self.peek().kind == "number":
            return -_number(self.take().text)
        if token.kind == "punct" and token.text == "{":
            return self.table()
        if token.kind == "name" and token.text in _KEYWORDS:
            return _KEYWORDS[token.text]
        if token.kind == "name" and token.text == "nil":
            return None
        raise self.error("expected a value", token)

    def table(self) -> LuaTable:
        """Parse a table body after its ``{``, through the closing ``}``.

        Returns:
            Key -> value; fields assigned nil are left out, as in Lua.
        """
        table: LuaTable = {}
        next_index = 1
        while not (self.peek().kind == "punct" and self.peek().text == "}"):
            key, value = self.field()
            if key is None:
                key, next_index = next_index, next_index + 1
            if value is not None:
                table[key] = value
            if self.peek().kind == "punct" and self.peek().text in (",", ";"):
                self.take()
            elif not (self.peek().kind == "punct" and self.peek().text == "}"):
                raise self.error("expected ',' or '}'")
        self.take()
        return table

    def field(self) -> tuple[object, LuaValue | None]:
        """Parse one table field.

        Returns:
            (key, value); key is None for an implicit array item.
        """
        token = self.peek()
        if token.kind == "punct" and token.text == "[":
            self.take()
            key = self.value()
            if key is None:
                raise self.error("table key is nil", token)
            if isinstance(key, float) and key.is_integer():
                key = int(key)   # Lua normalizes [1.0] to [1]
            self.expect("]")
            self.expect("=")
            return key, self.value()
        if token.kind == "name" and self.peek(1).kind == "punct" and self.peek(1).text == "=":
            self.take()
            self.take()
            return token.text, self.value()
        return None, self.value()


def _number(text: str) -> int | float:
    """Convert a Lua number literal.

    Args:
        text: e.g. "42", "0.25", "1e3", "0x1F".

    Returns:
        int when the literal has no fraction or exponent (or is hex), else float.
    """
    if text[:2] in ("0x", "0X"):
        return int(text, 16)
    if any(c in text for c in ".eE"):
        return float(text)
    return int(text)


def parse_lua(text: str) -> dict[str, LuaValue | None]:
    """Parse a SavedVariables file's Lua into Python values.

    Args:
        text: The file's contents.

    Returns:
        Global name -> value. Tables become dicts (array items keyed 1..n),
        nil becomes None.

    Raises:
        SavedVarsParseError: When the text is not valid SavedVariables Lua.
    """
    return _Parser(text).assignments()
