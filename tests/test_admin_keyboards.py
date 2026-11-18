"""Admin inline keyboard structure."""

from bot.keyboards.admin import participant_card


def test_participant_card_puts_chat_button_first():
    markup = participant_card(chat_url="tg://user?id=7617766886")

    contact = markup.inline_keyboard[0][0]
    assert contact.text == "✍️ Написать"
    assert contact.url == "tg://user?id=7617766886"
    assert markup.inline_keyboard[-1][0].callback_data == "adm:participants"


def test_participant_card_without_chat_url_keeps_back_button():
    markup = participant_card()

    assert len(markup.inline_keyboard) == 1
    assert markup.inline_keyboard[0][0].callback_data == "adm:participants"
