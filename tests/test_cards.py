from types import SimpleNamespace

from aiogram.exceptions import TelegramBadRequest

from bot.db.enums import ReceiptType
from bot.services.cards import edit_review_card, finalize_cancelled, send_receipt_to_chat


class FakeStorage:
    async def get(self, key: str) -> bytes:
        return b"%PDF-1.4 fallback"


class FakeBot:
    def __init__(self) -> None:
        self.documents = []

    async def send_document(self, chat_id, document, **kwargs):
        self.documents.append(document)
        if isinstance(document, str):
            raise TelegramBadRequest(method=None, message="wrong file identifier")
        return SimpleNamespace(message_id=1)


async def test_file_id_failure_falls_back_to_rustfs_object():
    application = SimpleNamespace(
        receipt_type=ReceiptType.PDF,
        receipt_file_id="telegram-file-id",
        receipt_object_key="receipts/1/2/unique.pdf",
    )
    bot = FakeBot()
    await send_receipt_to_chat(bot, chat_id=10, application=application, storage=FakeStorage())
    assert len(bot.documents) == 2
    assert bot.documents[0] == "telegram-file-id"
    assert getattr(bot.documents[1], "data", None) == b"%PDF-1.4 fallback"


class EditBot:
    def __init__(self) -> None:
        self.texts: list[str] = []

    async def edit_message_caption(self, **kwargs):
        raise TelegramBadRequest(method=None, message="there is no caption")

    async def edit_message_text(self, **kwargs):
        self.texts.append(kwargs["text"])
        return SimpleNamespace(message_id=2)


async def test_long_card_text_fallback_can_be_edited_as_text():
    application = SimpleNamespace(
        public_id="A-2026-000123",
        card_chat_id=10,
        card_message_id=11,
        receipt_type=ReceiptType.PDF,
    )
    bot = EditBot()
    await edit_review_card(bot, application, "*Карточка* " + "x" * 1500)
    assert bot.texts
    assert "Карточка" in bot.texts[0]


class CancellationBot:
    def __init__(self) -> None:
        self.captions: list[str] = []

    async def edit_message_caption(self, **kwargs):
        self.captions.append(kwargs["caption"])
        return SimpleNamespace(message_id=2)

    async def edit_message_text(self, **kwargs):
        return SimpleNamespace(message_id=2)


class DeleteStorage:
    def __init__(self) -> None:
        self.deleted: list[str] = []

    async def delete(self, key: str) -> None:
        self.deleted.append(key)


async def test_finalize_cancelled_edits_card_and_deletes_receipt():
    application = SimpleNamespace(
        public_id="A-2026-000123",
        card_chat_id=10,
        card_message_id=11,
        receipt_type=ReceiptType.PDF,
        receipt_object_key="receipts/1/2/file.pdf",
    )
    bot = CancellationBot()
    storage = DeleteStorage()

    await finalize_cancelled(bot, application, storage)

    assert bot.captions
    assert "отменена участником" in bot.captions[0]
    assert storage.deleted == ["receipts/1/2/file.pdf"]
