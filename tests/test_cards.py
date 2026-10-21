from types import SimpleNamespace

from aiogram.exceptions import TelegramBadRequest

from bot.db.enums import ReceiptType
from bot.services.cards import send_receipt_to_chat


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
