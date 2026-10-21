"""URL validation helpers for admin-entered links."""

from __future__ import annotations

from urllib.parse import urlsplit


def is_valid_http_url(value: str) -> bool:
    """Return whether ``value`` is an absolute HTTP(S) URL."""
    if not value or any(char.isspace() for char in value):
        return False
    parsed = urlsplit(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def normalize_http_url(value: object) -> str:
    """Validate a required link and return it without surrounding spaces."""
    text = "" if value is None else str(value).strip()
    if not is_valid_http_url(text):
        raise ValueError("Ссылка должна начинаться с http:// или https://.")
    return text


def normalize_optional_http_url(value: object) -> str | None:
    """Validate an optional link; an empty value becomes ``None``."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return normalize_http_url(text)
