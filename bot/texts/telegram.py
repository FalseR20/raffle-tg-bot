"""Convert Telegram message entities into MarkdownV2 for admin-entered text."""

from __future__ import annotations

import itertools
from collections.abc import Sequence

from aiogram.types import MessageEntity

from bot.texts.markdown import escape_markdown_v2, escape_url

_MARKERS = {
    "bold": ("*", "*"),
    "italic": ("_", "_"),
    "underline": ("__", "__"),
    "strikethrough": ("~", "~"),
    "code": ("`", "`"),
    "pre": ("```", "```"),
}

_RAW_TYPES = {"code", "pre"}


def _utf16_to_python_index(text: str, utf16_index: int) -> int:
    """Convert Telegram's UTF-16 entity offset to a Python string index."""
    if utf16_index <= 0:
        return 0
    units = 0
    for index, char in enumerate(text):
        if units >= utf16_index:
            return index
        units += 2 if ord(char) > 0xFFFF else 1
    return len(text)


def _entity_spans(
    text: str, entities: Sequence[MessageEntity]
) -> list[tuple[int, int, MessageEntity]] | None:
    spans: list[tuple[int, int, MessageEntity]] = []
    for entity in entities:
        start = _utf16_to_python_index(text, entity.offset)
        end = _utf16_to_python_index(text, entity.offset + entity.length)
        if start == end:
            continue
        spans.append((start, end, entity))
    spans.sort(key=lambda item: (item[0], -item[1]))
    for previous, current in itertools.pairwise(spans):
        if current[0] < previous[1]:
            # Nested/overlapping entities cannot be represented reliably here.
            return None
    return spans


def _render_entity(content: str, entity: MessageEntity) -> str:
    if entity.type in _RAW_TYPES:
        # Code spans are literal; avoid producing broken markup from backticks.
        safe = content.replace("`", "'")
        marker = "`" if entity.type == "code" else "```"
        return f"{marker}{safe}{marker}"

    if entity.type in {"text_link", "url"} and entity.url:
        return f"[{escape_markdown_v2(content)}]({escape_url(entity.url)})"

    if entity.type == "text_mention" and entity.user is not None:
        return f"[{escape_markdown_v2(content)}](tg://user?id={entity.user.id})"

    markers = _MARKERS.get(entity.type)
    if markers is None:
        return escape_markdown_v2(content)
    opening, closing = markers
    return f"{opening}{escape_markdown_v2(content)}{closing}"


def markdown_from_entities(text: str, entities: Sequence[MessageEntity] | None) -> str:
    """Rebuild MarkdownV2 from a Telegram message with formatting entities.

    Plain messages without entities are returned unchanged, so admins can still
    type raw MarkdownV2 manually. Messages formatted by the Telegram client keep
    their formatting (especially tap-to-copy code spans).
    """
    if not entities:
        return text
    spans = _entity_spans(text, entities)
    if spans is None:
        return text

    result: list[str] = []
    cursor = 0
    for start, end, entity in spans:
        if start > cursor:
            result.append(escape_markdown_v2(text[cursor:start]))
        result.append(_render_entity(text[start:end], entity))
        cursor = end
    if cursor < len(text):
        result.append(escape_markdown_v2(text[cursor:]))
    return "".join(result)
