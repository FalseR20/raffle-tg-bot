"""Participant-facing inline keyboards."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def consent_keyboard(*, age_required: bool) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text="✅ Мне 18+" if age_required else "✅ Принимаю",
                callback_data="consent:accept",
            )
        ],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="consent:cancel")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def publish_consent_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Да, можно опубликовать имя", callback_data="publish:yes")],
            [InlineKeyboardButton(text="Нет, только номер билета", callback_data="publish:no")],
        ]
    )


def organizations_keyboard(
    organizations: list[tuple[int, str]], *, include_my: bool = True
) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=name[:60], callback_data=f"org:{organization_id}")]
        for organization_id, name in organizations
    ]
    if include_my:
        rows.append([InlineKeyboardButton(text="🎟 Мои заявки", callback_data="my:list")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def payment_methods_keyboard(methods: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=title[:60], callback_data=f"method:{method_id}")]
        for method_id, title in methods
    ]
    rows.append([InlineKeyboardButton(text="↩️ Назад", callback_data="flow:back_orgs")])
    rows.append([InlineKeyboardButton(text="❌ Отменить", callback_data="flow:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def instructions_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="↩️ Другой способ оплаты", callback_data="flow:back_methods"
                )
            ],
            [InlineKeyboardButton(text="❌ Отменить", callback_data="flow:cancel")],
        ]
    )


def amount_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Изменить сумму", callback_data="flow:change_amount")],
            [InlineKeyboardButton(text="❌ Отменить", callback_data="flow:cancel")],
        ]
    )


def receipt_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Изменить сумму", callback_data="flow:change_amount")],
            [InlineKeyboardButton(text="❌ Отменить", callback_data="flow:cancel")],
        ]
    )


def draft_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="▶️ Продолжить", callback_data="draft:continue")],
            [InlineKeyboardButton(text="🔄 Начать заново", callback_data="draft:restart")],
            [InlineKeyboardButton(text="❌ Отменить черновик", callback_data="draft:cancel")],
        ]
    )


def application_actions(application_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ Отменить заявку", callback_data=f"my:cancel:{application_id}"
                )
            ],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="my:list")],
        ]
    )


def cancel_confirmation(application_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Да, отменить", callback_data=f"my:cancel_yes:{application_id}"
                ),
                InlineKeyboardButton(text="Нет", callback_data="my:list"),
            ]
        ]
    )


def channel_keyboard(
    channel_url: str | None, channel_title: str | None
) -> InlineKeyboardMarkup | None:
    if not channel_url:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"📣 {channel_title or 'Канал'}", url=channel_url)]
        ]
    )


def my_applications_keyboard(*, can_send_more: bool) -> InlineKeyboardMarkup:
    rows = []
    if can_send_more:
        rows.append(
            [InlineKeyboardButton(text="➕ Отправить ещё заявку", callback_data="flow:new")]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None
