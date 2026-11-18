"""Admin inline keyboard structure."""

from types import SimpleNamespace

from bot.keyboards.admin import participant_card
from bot.services.users import has_direct_chat


def test_participant_card_puts_chat_button_first():
    markup = participant_card(chat_url="tg://user?id=7617766886")

    contact = markup.inline_keyboard[0][0]
    assert contact.text == "✍️ Написать"
    assert contact.url == "tg://user?id=7617766886"
    assert markup.inline_keyboard[-1][0].callback_data == "adm:participants"


def test_participant_card_prefers_relay_when_direct_chat_is_restricted():
    markup = participant_card(contact_callback="adm:talk:7617766886")

    contact = markup.inline_keyboard[0][0]
    assert contact.text == "✉️ Написать через бота"
    assert contact.callback_data == "adm:talk:7617766886"
    assert contact.url is None
    assert markup.inline_keyboard[-1][0].callback_data == "adm:participants"


def test_participant_card_without_chat_url_keeps_back_button():
    markup = participant_card()

    assert len(markup.inline_keyboard) == 1
    assert markup.inline_keyboard[0][0].callback_data == "adm:participants"


class ChatBot:
    def __init__(self, *, restricted: bool) -> None:
        self.restricted = restricted
        self.calls: list[int] = []

    async def get_chat(self, user_id: int):
        self.calls.append(user_id)
        return SimpleNamespace(id=user_id, has_private_forwards=self.restricted)


def _participant(username: str | None) -> SimpleNamespace:
    return SimpleNamespace(id=7617766886, username=username)


async def test_direct_chat_available_trusts_username_without_api_call():
    bot = ChatBot(restricted=True)

    assert await has_direct_chat(bot, _participant("alex")) is True
    assert bot.calls == []


async def test_direct_chat_available_detects_forward_restriction():
    bot = ChatBot(restricted=True)

    assert await has_direct_chat(bot, _participant(None)) is False
    assert bot.calls == [7617766886]


async def test_direct_chat_available_allows_open_privacy():
    bot = ChatBot(restricted=False)

    assert await has_direct_chat(bot, _participant(None)) is True
