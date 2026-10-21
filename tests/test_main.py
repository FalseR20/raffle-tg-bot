from aiogram.types import BotCommand

from bot.main import bot_commands


def test_bot_commands_are_aiogram_models():
    commands = bot_commands()
    assert all(isinstance(command, BotCommand) for command in commands)
    assert [command.command for command in commands] == ["start", "my", "help", "admin"]
    assert all(command.description for command in commands)


def test_finished_raffle_card_has_winners_button():
    from bot.db.enums import RaffleStatus
    from bot.keyboards.admin import raffle_card

    callbacks = [
        button.callback_data
        for row in raffle_card(7, RaffleStatus.FINISHED, can_manage=True).inline_keyboard
        for button in row
    ]
    assert "adm:winners:7" in callbacks

    archived_callbacks = [
        button.callback_data
        for row in raffle_card(8, RaffleStatus.ARCHIVED, can_manage=True).inline_keyboard
        for button in row
    ]
    assert "adm:winners:8" in archived_callbacks
