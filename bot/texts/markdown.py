"""Telegram MarkdownV2 helpers.

The bot never uses HTML. Dynamic values must always pass through
:func:`escape_markdown_v2`. Admin-entered payment instructions are validated
before saving so that Telegram does not reject them at send time; if Telegram
still rejects markup, callers can fall back to :func:`to_plain_text`.
"""

from __future__ import annotations

import re

SPECIAL_CHARS = frozenset("_*[]()~`>#+-=|{}.!\\")
MESSAGE_LIMIT = 4096
CAPTION_LIMIT = 1024


class MarkdownError(ValueError):
    """Raised when text is not valid Telegram MarkdownV2."""


def escape_markdown_v2(value: object) -> str:
    text = "" if value is None else str(value)
    return "".join(f"\\{char}" if char in SPECIAL_CHARS else char for char in text)


def escape_url(value: object) -> str:
    """Escape only the characters Telegram requires inside a link URL."""
    text = "" if value is None else str(value)
    return text.replace("\\", "\\\\").replace(")", "\\)")


def _find_unescaped(text: str, char: str, start: int) -> int:
    index = start
    while index < len(text):
        if text[index] == "\\":
            index += 2
            continue
        if text.startswith(char, index):
            return index
        index += 1
    return -1


def _validate_url(url: str) -> None:
    if not url:
        raise MarkdownError("link URL is empty")
    if any(char.isspace() for char in url):
        raise MarkdownError("link URL contains whitespace")
    index = 0
    while index < len(url):
        char = url[index]
        if char == "\\":
            if index + 1 >= len(url):
                raise MarkdownError("dangling escape in link URL")
            index += 2
            continue
        if char in "()[]":
            raise MarkdownError(f"unescaped {char!r} in link URL")
        index += 1


def validate_markdown_v2(text: str) -> None:
    """Validate the subset of MarkdownV2 accepted by the bot.

    Plain reserved characters must be escaped, entities must be balanced and
    code spans and links must be well formed.
    """
    if not isinstance(text, str):
        raise MarkdownError("markdown text must be a string")
    index = 0
    open_markers: list[str] = []
    length = len(text)

    while index < length:
        char = text[index]

        if char == "\\":
            if index + 1 >= length:
                raise MarkdownError("dangling escape at end of text")
            escaped = text[index + 1]
            if not 1 <= ord(escaped) <= 126:
                raise MarkdownError("only ASCII characters can be escaped")
            index += 2
            continue

        if char == "`":
            delimiter = "```" if text.startswith("```", index) else "`"
            end = _find_unescaped(text, delimiter, index + len(delimiter))
            if end < 0:
                raise MarkdownError("unclosed code span")
            if end == index + len(delimiter):
                raise MarkdownError("empty code span")
            index = end + len(delimiter)
            continue

        if char == "*":
            _toggle(open_markers, "*")
            index += 1
            continue

        if char == "~":
            _toggle(open_markers, "~")
            index += 1
            continue

        if char == "_":
            if text.startswith("__", index):
                _toggle(open_markers, "__")
                index += 2
            else:
                _toggle(open_markers, "_")
                index += 1
            continue

        if char == "[":
            index = _consume_link(text, index)
            continue

        if char in SPECIAL_CHARS:
            raise MarkdownError(f"unescaped special character {char!r} at position {index}")

        index += 1

    if open_markers:
        raise MarkdownError(f"unclosed formatting marker {open_markers[-1]!r}")


def _toggle(open_markers: list[str], marker: str) -> None:
    if open_markers and open_markers[-1] == marker:
        open_markers.pop()
    else:
        open_markers.append(marker)


def _consume_link(text: str, start: int) -> int:
    close_bracket = _find_unescaped(text, "]", start + 1)
    if close_bracket < 0:
        raise MarkdownError("unclosed link text")
    if close_bracket == start + 1:
        raise MarkdownError("empty link text")
    if close_bracket + 1 >= len(text) or text[close_bracket + 1] != "(":
        raise MarkdownError("link must be followed by a URL in parentheses")
    close_paren = _find_unescaped(text, ")", close_bracket + 2)
    if close_paren < 0:
        raise MarkdownError("unclosed link URL")

    link_text = text[start + 1 : close_bracket]
    if any(char in link_text for char in "*_~`[]"):
        raise MarkdownError("nested markup inside link text is not supported")
    _validate_url(text[close_bracket + 2 : close_paren])
    return close_paren + 1


def is_valid_markdown_v2(text: str) -> bool:
    try:
        validate_markdown_v2(text)
    except MarkdownError:
        return False
    return True


_LINK_RE = re.compile(r"\[([^\]\\]*(?:\\.[^\]\\]*)*)\]\(([^)\\]*(?:\\.[^)\\]*)*)\)")


def to_plain_text(text: str) -> str:
    """Best-effort conversion of MarkdownV2 to plain text for fallback sends."""
    converted = _LINK_RE.sub(lambda match: f"{match.group(1)} ({match.group(2)})", text)
    converted = re.sub(r"\\(.)", r"\1", converted)
    return (
        converted.replace("*", "")
        .replace("~", "")
        .replace("`", "")
        .replace("__", "")
        .replace("_", "")
    )


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"
