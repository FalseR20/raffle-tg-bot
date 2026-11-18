"""Unit tests for participant profile helpers."""

from types import SimpleNamespace

from bot.services.users import chat_url, display_name, mention


def _user(user_id: int, username: str | None, first_name: str = "Иван") -> SimpleNamespace:
    return SimpleNamespace(id=user_id, username=username, first_name=first_name, last_name=None)


def test_chat_url_prefers_username():
    assert chat_url(_user(42, "someone")) == "https://t.me/someone"


def test_chat_url_falls_back_to_user_id():
    assert chat_url(_user(7617766886, None, "Александр")) == "tg://user?id=7617766886"


def test_mention_prefers_username():
    assert mention(_user(42, "someone")) == "@someone"


def test_mention_links_names_without_username():
    assert mention(_user(42, None)) == "[Иван](tg://user?id=42)"


def test_display_name_falls_back_to_id():
    assert display_name(_user(42, None, "")) == "ID 42"
