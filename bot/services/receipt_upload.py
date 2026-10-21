"""Receipt download, validation and upload orchestration."""

from __future__ import annotations

import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.enums import ReceiptType
from bot.db.models import Application
from bot.db.repositories import applications as apps_repo
from bot.db.repositories import audit as audit_repo
from bot.services import receipts as receipts_service
from bot.services.applications import attach_receipt
from bot.services.errors import InvalidStateError, ReceiptError, StorageError
from bot.services.storage import ReceiptStorage

DownloadFn = Callable[[str], Awaitable[bytes]]


@dataclass(frozen=True)
class IncomingReceipt:
    file_id: str
    file_unique_id: str
    size: int
    is_photo: bool
    mime: str | None = None
    file_name: str | None = None


@dataclass(frozen=True)
class UploadedReceipt:
    application: Application
    object_key: str
    receipt_type: ReceiptType


async def accept_receipt(
    session: AsyncSession,
    *,
    application: Application,
    user_id: int,
    incoming: IncomingReceipt,
    max_bytes: int,
    download: DownloadFn,
    storage: ReceiptStorage,
    now: datetime,
) -> UploadedReceipt:
    """Validate, upload to RustFS and only then move the application to review.

    The receipt is held in memory only; no permanent local receipt directory
    exists. On any failure the application status is left untouched.
    """
    current = await apps_repo.get(session, application.id)
    if current is None or current.user_id != user_id:
        raise InvalidStateError("Заявка не найдена.")
    if current.status.value not in {"awaiting_receipt", "revision_requested"}:
        raise InvalidStateError("Сейчас нельзя прикрепить чек.")

    data = await _download(download, incoming.file_id)
    if len(data) > max_bytes:
        raise ReceiptError("Файл слишком большой.")

    if incoming.is_photo:
        receipts_service.inspect_photo(size=len(data), max_bytes=max_bytes)
        info = receipts_service.ReceiptInfo(
            receipt_type=ReceiptType.PHOTO,
            mime="image/jpeg",
            extension="jpg",
            size=len(data),
        )
        if not data.startswith(receipts_service.JPEG_MAGIC):
            raise ReceiptError("Фотография повреждена.")
    else:
        info = receipts_service.inspect_document(
            data=data,
            file_name=incoming.file_name,
            declared_mime=incoming.mime,
            size=len(data),
            max_bytes=max_bytes,
        )

    duplicate = await apps_repo.duplicate_receipt(
        session, incoming.file_unique_id, exclude_id=current.id
    )
    if duplicate is not None:
        raise ReceiptError("Этот чек уже был загружен в другой заявке.")

    object_key = receipts_service.build_object_key(
        raffle_id=current.raffle_id,
        application_id=current.id,
        file_unique_id=incoming.file_unique_id,
        extension=info.extension,
    )
    etag = await storage.put(object_key, data, content_type=info.mime)

    locked = await apps_repo.get_locked(session, application.id)
    if locked is None or locked.status.value not in {"awaiting_receipt", "revision_requested"}:
        await storage.delete(object_key)
        raise InvalidStateError("Статус заявки изменился, чек не принят.")

    try:
        updated = await attach_receipt(
            session,
            application=locked,
            receipt_type=info.receipt_type,
            file_id=incoming.file_id,
            file_unique_id=incoming.file_unique_id,
            object_key=object_key,
            etag=etag,
            mime=info.mime,
            size=info.size,
            now=now,
        )
    except Exception:
        with contextlib.suppress(StorageError):
            await storage.delete(object_key)
        raise

    await audit_repo.log(
        session,
        entity_type="application",
        entity_id=updated.id,
        application_id=updated.id,
        raffle_id=updated.raffle_id,
        actor_id=user_id,
        action="receipt_stored",
        payload={"object_key": object_key, "etag": etag},
        at=now,
    )
    return UploadedReceipt(
        application=updated, object_key=object_key, receipt_type=info.receipt_type
    )


async def _download(download: DownloadFn, file_id: str) -> bytes:
    try:
        data = await download(file_id)
    except Exception as exc:
        raise ReceiptError("Не удалось скачать файл из Telegram. Попробуйте ещё раз.") from exc
    if not data:
        raise ReceiptError("Не удалось скачать файл из Telegram.")
    return data
