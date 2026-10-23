from aiogram.types import BotCommand

from bot.db.enums import RaffleStatus
from bot.main import bot_commands
from bot.services.permissions import Permissions


def test_bot_commands_are_aiogram_models():
    commands = bot_commands()
    assert all(isinstance(command, BotCommand) for command in commands)
    assert [command.command for command in commands] == ["start", "my", "help", "admin"]
    assert all(command.description for command in commands)


def test_main_menu_has_no_global_draw_section():
    from bot.keyboards.admin import main_menu

    callbacks = [
        button.callback_data
        for row in main_menu(Permissions(user_id=100, is_admin=True)).inline_keyboard
        for button in row
    ]
    assert "adm:draw" not in callbacks


def test_winners_menu_has_random_draw_and_returns_to_raffle():
    from bot.keyboards.admin import winners_menu

    callbacks = [
        button.callback_data
        for row in winners_menu(7, random_enabled=True, allow_random=True).inline_keyboard
        for button in row
    ]
    assert "adm:draw_random_do:7" in callbacks
    assert "adm:raffle:7" in callbacks


def test_finished_raffle_card_has_winners_button():
    from bot.db.enums import RaffleStatus
    from bot.keyboards.admin import raffle_card

    callbacks = [
        button.callback_data
        for row in raffle_card(7, RaffleStatus.FINISHED, can_manage=True).inline_keyboard
        for button in row
    ]
    assert "adm:winners:7" in callbacks
    assert "adm:raffle_del:7" not in callbacks

    archived_callbacks = [
        button.callback_data
        for row in raffle_card(8, RaffleStatus.ARCHIVED, can_manage=True).inline_keyboard
        for button in row
    ]
    assert "adm:winners:8" in archived_callbacks
    assert "adm:raffle_del:8" in archived_callbacks

    draft_callbacks = [
        button.callback_data
        for row in raffle_card(9, RaffleStatus.DRAFT, can_manage=True).inline_keyboard
        for button in row
    ]
    assert "adm:raffle_del:9" in draft_callbacks


def test_main_menu_shows_shortcuts_to_non_archived_raffles():
    from bot.keyboards.admin import main_menu

    rows = main_menu(
        Permissions(user_id=100, is_admin=True),
        [
            (7, "Активный розыгрыш", RaffleStatus.ACTIVE),
            (8, "Завершённый розыгрыш", RaffleStatus.FINISHED),
        ],
    ).inline_keyboard
    callbacks = [[button.callback_data for button in row] for row in rows]
    assert callbacks[0] == ["adm:raffle:7"]
    assert callbacks[1] == ["adm:raffle:8"]
    assert callbacks[2] == ["adm:raffles", "adm:apps"]


def test_org_rep_menu_does_not_show_raffle_shortcuts():
    from bot.keyboards.admin import main_menu

    rows = main_menu(
        Permissions(user_id=200, org_rep_ids=frozenset({1})),
        [(7, "Активный розыгрыш", RaffleStatus.ACTIVE)],
    ).inline_keyboard
    callbacks = [button.callback_data for row in rows for button in row]
    assert "adm:raffle:7" not in callbacks
