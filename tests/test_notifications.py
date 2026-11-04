from types import SimpleNamespace

from aiogram.exceptions import TelegramBadRequest

from bot.services.notifications import editing_target, send_text


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


class EditBot:
    def __init__(self, *, edit_error: str | None = None) -> None:
        self.sent: list[dict] = []
        self.edits: list[dict] = []
        self.edit_error = edit_error

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append({"chat_id": chat_id, "text": text, **kwargs})
        return SimpleNamespace(message_id=len(self.sent) + 100)

    async def edit_message_text(self, text, chat_id, message_id, **kwargs):
        if self.edit_error is not None:
            raise TelegramBadRequest(method=None, message=self.edit_error)
        self.edits.append({"chat_id": chat_id, "message_id": message_id, "text": text, **kwargs})
        return SimpleNamespace(message_id=message_id)


async def test_editing_target_edits_instead_of_sending():
    bot = EditBot()
    with editing_target(123, 55):
        result = await send_text(bot, 123, "*Новый экран*", reply_markup=object())

    assert result.ok
    assert not bot.sent
    assert len(bot.edits) == 1
    assert bot.edits[0]["chat_id"] == 123
    assert bot.edits[0]["message_id"] == 55
    assert bot.edits[0]["parse_mode"] == "MarkdownV2"


async def test_editing_target_ignores_other_chats_and_explicit_new_messages():
    bot = EditBot()
    with editing_target(123, 55):
        await send_text(bot, 999, "*Другой чат*")
        await send_text(bot, 123, "*Отдельное*", new_message=True)

    assert [call["chat_id"] for call in bot.sent] == [999, 123]
    assert not bot.edits


async def test_not_modified_edit_is_treated_as_success():
    bot = EditBot(edit_error="Bad Request: message is not modified")
    with editing_target(123, 55):
        result = await send_text(bot, 123, "*Тот же экран*")

    assert result.ok
    assert result.unchanged
    assert not bot.sent


async def test_uneditable_message_falls_back_to_new_message():
    bot = EditBot(edit_error="Bad Request: message can't be edited")
    with editing_target(123, 55):
        result = await send_text(bot, 123, "*Новый экран*")

    assert result.ok
    assert len(bot.sent) == 1
    assert bot.sent[0]["text"] == "*Новый экран*"
