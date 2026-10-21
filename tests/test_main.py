from aiogram.types import BotCommand

from bot.main import bot_commands


def test_bot_commands_are_aiogram_models():
    commands = bot_commands()
    assert all(isinstance(command, BotCommand) for command in commands)
    assert [command.command for command in commands] == ["start", "my", "help", "admin"]
    assert all(command.description for command in commands)
