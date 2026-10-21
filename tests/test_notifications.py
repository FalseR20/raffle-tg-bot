from types import SimpleNamespace

from aiogram.exceptions import TelegramBadRequest

from bot.services.notifications import send_text


class FakeBot:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def send_message(self, chat_id, text, **kwargs):
        self.calls.append({"chat_id": chat_id, "text": text, **kwargs})
        if kwargs.get("parse_mode") == "MarkdownV2":
            raise TelegramBadRequest(method=None, message="Bad Request: can't parse entities")
        return SimpleNamespace(message_id=1)


async def test_markdown_failure_falls_back_to_plain_text():
    bot = FakeBot()
    result = await send_text(bot, 123, "*Жирный* текст\\.")
    assert result.ok
    assert result.used_plain_text
    assert len(bot.calls) == 2
    assert bot.calls[0]["parse_mode"] == "MarkdownV2"
    assert bot.calls[1]["parse_mode"] is None
    assert bot.calls[1]["text"] == "Жирный текст."


async def test_successful_markdown_is_not_retried():
    class WorkingBot(FakeBot):
        async def send_message(self, chat_id, text, **kwargs):
            self.calls.append({"chat_id": chat_id, "text": text, **kwargs})
            return SimpleNamespace(message_id=2)

    bot = WorkingBot()
    result = await send_text(bot, 123, "*Текст*")
    assert result.ok
    assert not result.used_plain_text
    assert len(bot.calls) == 1


async def test_invalid_markdown_is_sent_plain_without_telegram_roundtrip():
    bot = FakeBot()
    result = await send_text(bot, 123, "Скобка (не экранирована)")
    assert result.ok
    assert result.used_plain_text
    assert len(bot.calls) == 1
    assert bot.calls[0]["parse_mode"] is None
    assert "(" in bot.calls[0]["text"]


async def test_invalid_keyboard_is_retried_without_markup():
    class BadKeyboardBot:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        async def send_message(self, chat_id, text, **kwargs):
            self.calls.append({"chat_id": chat_id, "text": text, **kwargs})
            if "reply_markup" in kwargs:
                raise TelegramBadRequest(method=None, message="Bad Request: BUTTON_URL_INVALID")
            return SimpleNamespace(message_id=3)

    bot = BadKeyboardBot()
    result = await send_text(bot, 123, "*Текст*", reply_markup=object())
    assert result.ok
    assert not result.used_plain_text
    assert len(bot.calls) == 2
    assert "reply_markup" not in bot.calls[1]
