"""Receipt upload handlers for participants."""

from __future__ import annotations

import io
from datetime import datetime
from decimal import Decimal

from aiogram import Bot, F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import User
from bot.handlers.participant import _current_draft
from bot.services import notifications
from bot.services.cards import send_application_card
from bot.services.errors import ServiceError
from bot.services.ratelimit import RateLimiter
from bot.services.receipt_upload import IncomingReceipt, accept_receipt
from bot.services.settings import RuntimeSettings
from bot.services.storage import ReceiptStorage
from bot.states import ParticipantFlow
from bot.texts import ru
from bot.texts.markdown import escape_markdown_v2

router = Router(name="participant_receipts")


@router.message(F.photo | F.document)
async def on_receipt(
    message: Message,
    bot: Bot,
    session: AsyncSession,
    user: User,
    runtime_settings: RuntimeSettings,
    state: FSMContext,
    storage: ReceiptStorage,
    limiter: RateLimiter,
    env_settings,
    now: datetime,
) -> None:
    if message.media_group_id:
        await notifications.send_text(
            bot,
            message.chat.id,
            "Пришлите, пожалуйста, один чек одним сообщением, без альбома\\.",
        )
        return
    if not limiter.allow("receipt", user.id, limit=5, window_seconds=600):
        await notifications.send_text(
            bot, message.chat.id, "Слишком много загрузок\\. Попробуйте позже\\."
        )
        return

    draft = await _current_draft(session, user, state)
    if draft is None:
        await state.clear()
        await notifications.send_text(
            bot, message.chat.id, "Сейчас нет заявки, ожидающей чек\\. Начните с /start\\."
        )
        return

    if message.photo:
        largest = message.photo[-1]
        incoming = IncomingReceipt(
            file_id=largest.file_id,
            file_unique_id=largest.file_unique_id,
            size=largest.file_size or 0,
            is_photo=True,
        )
    else:
        document = message.document
        if document is None:
            return
        incoming = IncomingReceipt(
            file_id=document.file_id,
            file_unique_id=document.file_unique_id,
            size=document.file_size or 0,
            is_photo=False,
            mime=document.mime_type,
            file_name=document.file_name,
        )

    if incoming.size > env_settings.receipt_max_bytes:
        await notifications.send_text(
            bot,
            message.chat.id,
            ru.file_too_large_text(env_settings.receipt_max_mb),
        )
        return

    async def download(file_id: str) -> bytes:
        buffer = io.BytesIO()
        await bot.download(file_id, destination=buffer)
        return buffer.getvalue()

    try:
        result = await accept_receipt(
            session,
            application=draft,
            user_id=user.id,
            incoming=incoming,
            max_bytes=env_settings.receipt_max_bytes,
            download=download,
            storage=storage,
            now=now,
        )
        await session.commit()
    except ServiceError as exc:
        from bot.services.errors import StorageError

        if isinstance(exc, StorageError):
            await notifications.notify_admins(
                bot,
                env_settings.admin_id_set,
                "⚠️ Ошибка RustFS при загрузке чека\\. Заявка не переведена на проверку\\.",
            )
        await notifications.send_text(bot, message.chat.id, escape_markdown_v2(exc.user_message))
        return

    await state.clear()
    application = result.application
    await notifications.send_text(
        bot,
        message.chat.id,
        ru.pending_review_text(
            public_id=application.public_id,
            organization_name=application.organization.name,
            payment_method_title=application.payment_method_title or "—",
            amount=application.amount or Decimal("0.00"),
        ),
    )
    try:
        await send_application_card(
            session,
            bot,
            application=application,
            settings=runtime_settings,
            storage=storage,
            env_admin_ids=env_settings.admin_id_set,
            now=now,
        )
        await session.commit()
    except ServiceError as exc:
        await notifications.notify_admins(
            bot,
            env_settings.admin_id_set,
            "⚠️ Не удалось отправить карточку заявки "
            f"`{application.public_id}`: {escape_markdown_v2(str(exc))[:300]}",
        )


@router.message(StateFilter(ParticipantFlow.receipt))
async def on_invalid_receipt(message: Message, bot: Bot, state: FSMContext) -> None:
    if message.text and message.text.startswith("/"):
        return
    await notifications.send_text(bot, message.chat.id, ru.invalid_file_text())
