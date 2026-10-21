from aiogram.types import MessageEntity

from bot.texts.markdown import validate_markdown_v2
from bot.texts.telegram import markdown_from_entities


def test_client_code_formatting_becomes_backticks():
    prefix = "Там сям по номеру "
    text = prefix + "1234"
    entities = [MessageEntity(type="code", offset=len(prefix), length=4)]
    markdown = markdown_from_entities(text, entities)
    assert markdown == "Там сям по номеру `1234`"
    validate_markdown_v2(markdown)


def test_plain_segments_are_escaped_and_bold_is_preserved():
    text = "Переведите. Сумма 5 BYN"
    entities = [MessageEntity(type="bold", offset=12, length=7)]
    markdown = markdown_from_entities(text, entities)
    assert markdown == "Переведите\\. *Сумма 5* BYN"
    validate_markdown_v2(markdown)


def test_no_entities_keeps_manually_typed_markdown():
    text = "*Жирный* и `код`"
    assert markdown_from_entities(text, None) == text
    validate_markdown_v2(text)


def test_overlapping_entities_fall_back_safely():
    text = "abcdef"
    entities = [
        MessageEntity(type="bold", offset=0, length=4),
        MessageEntity(type="code", offset=2, length=4),
    ]
    assert markdown_from_entities(text, entities) == text
