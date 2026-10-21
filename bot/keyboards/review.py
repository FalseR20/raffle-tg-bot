"""Inline keyboards for review chat cards."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.texts.ru import REJECTION_REASONS


def review_actions(application_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Подтвердить", callback_data=f"rev:confirm:{application_id}"
                ),
                InlineKeyboardButton(
                    text="❌ Отклонить", callback_data=f"rev:reject:{application_id}"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📎 Запросить новый чек",
                    callback_data=f"rev:revision:{application_id}",
                )
            ],
        ]
    )


def confirm_second_step(application_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Да, выдать номера",
                    callback_data=f"rev:confirm_yes:{application_id}",
                ),
                InlineKeyboardButton(
                    text="❌ Отмена", callback_data=f"rev:cancel:{application_id}"
                ),
            ]
        ]
    )


def reject_reasons(application_id: int) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=label,
                callback_data=f"rev:reason:{application_id}:{code}",
            )
        ]
        for code, label in REJECTION_REASONS.items()
        if code != "other"
    ]
    rows.append(
        [
            InlineKeyboardButton(
                text="✏️ Другое",
                callback_data=f"rev:reason:{application_id}:other",
            )
        ]
    )
    rows.append([InlineKeyboardButton(text="↩️ Назад", callback_data=f"rev:back:{application_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def void_confirm(application_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Аннулировать", callback_data=f"rev:void_yes:{application_id}"
                ),
                InlineKeyboardButton(
                    text="❌ Отмена", callback_data=f"rev:cancel:{application_id}"
                ),
            ]
        ]
    )
