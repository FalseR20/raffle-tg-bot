"""End-to-end dispatch smoke tests with a fake Telegram transport."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from aiogram import Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Chat, Document, Message, Update
from aiogram.types import User as TgUser
from sqlalchemy import select

from bot.config import Settings
from bot.db.enums import ApplicationStatus
from bot.db.models import Application
from bot.handlers import admin, common, my, participant, receipts, review
from bot.middlewares.auth import AuthMiddleware
from bot.middlewares.db import DbSessionMiddleware
from bot.middlewares.throttle import ThrottleMiddleware
from bot.services.ratelimit import RateLimiter
from tests.helpers import make_organization, make_raffle

pytestmark = pytest.mark.integration


class FakeBot:
    def __init__(self) -> None:
        self.messages: list[tuple[int, str, dict]] = []
        self.answered: list[str] = []
        self.id = 999
        self.username = "test_bot"

    async def __call__(self, method, *args, **kwargs):
        name = type(method).__name__
        if name == "AnswerCallbackQuery":
            self.answered.append(getattr(method, "text", None) or "")
            return True
        if name == "SendMessage":
            return await self.send_message(
                method.chat_id,
                method.text or "",
                reply_markup=method.reply_markup,
            )
        return SimpleNamespace(message_id=1)

    async def send_message(self, chat_id, text, **kwargs):
        self.messages.append((chat_id, text, kwargs))
        return Message(
            message_id=len(self.messages),
            date=datetime.now(UTC),
            chat=Chat(id=chat_id, type="private"),
        )

    async def answer_callback_query(self, callback_query_id, text=None, **kwargs):
        self.answered.append(text or "")
        return True

    async def me(self):
        return SimpleNamespace(username=self.username)

    async def send_photo(self, *args, **kwargs):
        return SimpleNamespace(message_id=1)

    async def send_document(self, *args, **kwargs):
        return SimpleNamespace(message_id=1)

    async def get_chat(self, chat_id):
        return SimpleNamespace(id=chat_id)

    async def download(self, file_id, destination):
        destination.write(_pdf_bytes())
        return destination

    async def edit_message_caption(self, *args, **kwargs):
        return True

    async def edit_message_text(self, *args, **kwargs):
        return True


class FakeStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def put(self, key, data, *, content_type):
        self.objects[key] = data
        return "etag"

    async def get(self, key):
        return self.objects[key]

    async def delete(self, key):
        self.objects.pop(key, None)


def _pdf_bytes() -> bytes:
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    import io

    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _user(user_id: int, first_name: str = "Иван") -> TgUser:
    return TgUser(id=user_id, is_bot=False, first_name=first_name, username=f"u{user_id}")


def _message(user_id: int, text: str, update_id: int) -> Update:
    user = _user(user_id)
    message = Message(
        message_id=update_id,
        date=datetime.now(UTC),
        chat=Chat(id=user_id, type="private"),
        from_user=user,
        text=text,
    )
    return Update(update_id=update_id, message=message)


def _callback(user_id: int, data: str, update_id: int) -> Update:
    user = _user(user_id)
    callback = CallbackQuery(
        id=str(update_id),
        from_user=user,
        chat_instance="chat",
        data=data,
        message=Message(
            message_id=update_id,
            date=datetime.now(UTC),
            chat=Chat(id=user_id, type="private"),
            from_user=user,
            text="button",
        ),
    )
    return Update(update_id=update_id, callback_query=callback)


def build_dispatcher(session_factory, env: Settings):
    dp = Dispatcher(storage=MemoryStorage())
    dp.update.outer_middleware(DbSessionMiddleware(session_factory))
    dp.update.outer_middleware(AuthMiddleware(env))
    dp.update.outer_middleware(ThrottleMiddleware(RateLimiter()))
    dp.workflow_data.update(env_settings=env, limiter=RateLimiter(), storage=FakeStorage())
    dp.include_router(common.router)
    dp.include_router(admin.admin_router)
    dp.include_router(my.router)
    dp.include_router(review.router)
    dp.include_router(participant.router)
    dp.include_router(receipts.router)
    return dp


async def test_dispatch_smoke(session_factory, env_settings):
    bot = FakeBot()
    dp = build_dispatcher(session_factory, env_settings)

    await dp.feed_update(bot, _message(500, "/start", 1))
    assert bot.messages
    assert "Сейчас нет активного розыгрыша" in bot.messages[-1][1]

    async with session_factory() as session:
        org = await make_organization(session)
        await make_raffle(session, organizations=[org])
        await session.commit()

    bot.messages.clear()
    await dp.feed_update(bot, _message(500, "/start", 2))
    text = bot.messages[-1][1]
    assert "примите правила розыгрыша" in text
    assert "Правила розыгрыша" in text

    bot.messages.clear()
    await dp.feed_update(bot, _message(100, "/admin", 3))
    assert any("Админ" in text for _, text, _ in bot.messages)

    bot.messages.clear()
    await dp.feed_update(bot, _callback(100, "adm:menu", 4))
    assert any("Выберите раздел" in text for _, text, _ in bot.messages)

    bot.messages.clear()
    await dp.feed_update(bot, _message(500, "/admin", 5))
    assert any("нет доступа" in text for _, text, _ in bot.messages)

    bot.messages.clear()
    await dp.feed_update(bot, _callback(500, "consent:accept", 6))
    await dp.feed_update(bot, _callback(500, "publish:no", 7))
    assert any("Выберите организацию" in text for _, text, _ in bot.messages)

    bot.messages.clear()
    await dp.feed_update(bot, _callback(500, f"org:{org.id}", 8))
    assert any("Код платежа" in text for _, text, _ in bot.messages)

    bot.messages.clear()
    await dp.feed_update(bot, _message(500, "9", 9))
    assert any("После проверки чека" in text for _, text, _ in bot.messages)

    bot.messages.clear()
    pdf = _pdf_bytes()
    user = _user(500)
    document_message = Message(
        message_id=11,
        date=datetime.now(UTC),
        chat=Chat(id=500, type="private"),
        from_user=user,
        document=Document(
            file_id="file-id",
            file_unique_id="unique-id",
            file_name="receipt.pdf",
            mime_type="application/pdf",
            file_size=len(pdf),
        ),
    )
    await dp.feed_update(bot, Update(update_id=11, message=document_message))
    assert any("отправлена на проверку" in text for _, text, _ in bot.messages)

    bot.messages.clear()
    async with session_factory() as session:
        pending = (
            await session.execute(
                select(Application).where(
                    Application.user_id == 500,
                    Application.status == ApplicationStatus.PENDING_REVIEW,
                )
            )
        ).scalar_one()
        application_id = pending.id

    await dp.feed_update(bot, _callback(100, f"rev:confirm:{application_id}", 13))
    assert any("Подтвердить оплату" in text for _, text, _ in bot.messages)
    await dp.feed_update(bot, _callback(100, f"rev:confirm_yes:{application_id}", 14))
    assert any("Оплата подтверждена" in text for _, text, _ in bot.messages)

    bot.messages.clear()
    await dp.feed_update(bot, _message(500, "/my", 15))
    summary = "\n".join(text for _, text, _ in bot.messages)
    assert "Подтверждённая сумма" in summary
    assert "1 билет" in summary
