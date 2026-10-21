"""Shared keyboards."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def back_to_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ В меню", callback_data="adm:menu")]]
    )


def confirm_cancel(
    *, confirm_text: str, confirm_callback: str, cancel_callback: str = "adm:menu"
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=confirm_text, callback_data=confirm_callback)],
            [InlineKeyboardButton(text="❌ Отмена", callback_data=cancel_callback)],
        ]
    )
