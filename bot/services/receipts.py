"""Receipt validation: MIME, extension and magic bytes."""

from __future__ import annotations

import io
import re
from dataclasses import dataclass

from PIL import Image, UnidentifiedImageError
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from bot.db.enums import ReceiptType
from bot.services.errors import ReceiptError

PDF_MAGIC = b"%PDF-"
JPEG_MAGIC = b"\xff\xd8\xff"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

ALLOWED_IMAGE_MIMES = {"image/jpeg": "jpg", "image/png": "png"}
ALLOWED_PDF_MIMES = {"application/pdf": "pdf"}

_SAFE_UNIQUE_ID = re.compile(r"[^A-Za-z0-9_-]")


@dataclass(frozen=True)
class ReceiptInfo:
    receipt_type: ReceiptType
    mime: str
    extension: str
    size: int


def sanitize_unique_id(file_unique_id: str) -> str:
    sanitized = _SAFE_UNIQUE_ID.sub("", file_unique_id)[:64]
    if not sanitized:
        raise ReceiptError("Некорректный идентификатор файла.")
    return sanitized


def build_object_key(
    *, raffle_id: int, application_id: int, file_unique_id: str, extension: str
) -> str:
    """Object key is generated server-side, never from user input."""
    safe_extension = extension.lower().lstrip(".")
    if safe_extension not in {"jpg", "png", "pdf"}:
        raise ReceiptError("Неподдерживаемое расширение файла.")
    return (
        f"receipts/{int(raffle_id)}/{int(application_id)}/"
        f"{sanitize_unique_id(file_unique_id)}.{safe_extension}"
    )


def inspect_photo(*, size: int, max_bytes: int) -> ReceiptInfo:
    _assert_size(size, max_bytes)
    return ReceiptInfo(
        receipt_type=ReceiptType.PHOTO,
        mime="image/jpeg",
        extension="jpg",
        size=size,
    )


def inspect_document(
    *,
    data: bytes,
    file_name: str | None,
    declared_mime: str | None,
    size: int,
    max_bytes: int,
) -> ReceiptInfo:
    _assert_size(size, max_bytes)
    if len(data) != size:
        size = len(data)
    mime = (declared_mime or "").split(";")[0].strip().lower()
    extension = (
        (file_name or "").rsplit(".", 1)[-1].lower() if file_name and "." in file_name else ""
    )

    if data.startswith(PDF_MAGIC):
        if mime not in ALLOWED_PDF_MIMES and mime != "application/octet-stream":
            raise ReceiptError("Файл похож на PDF, но MIME-тип не совпадает.")
        if extension and extension != "pdf":
            raise ReceiptError("Расширение PDF-файла должно быть .pdf.")
        _validate_pdf(data)
        return ReceiptInfo(
            receipt_type=ReceiptType.PDF,
            mime="application/pdf",
            extension="pdf",
            size=size,
        )

    if data.startswith(JPEG_MAGIC):
        _validate_image(data, expected_format="JPEG")
        if mime not in ALLOWED_IMAGE_MIMES and mime != "application/octet-stream":
            raise ReceiptError("Файл похож на JPEG, но MIME-тип не совпадает.")
        if extension and extension not in {"jpg", "jpeg"}:
            raise ReceiptError("Расширение изображения должно быть .jpg или .jpeg.")
        return ReceiptInfo(
            receipt_type=ReceiptType.IMAGE,
            mime="image/jpeg",
            extension="jpg",
            size=size,
        )

    if data.startswith(PNG_MAGIC):
        _validate_image(data, expected_format="PNG")
        if mime not in ALLOWED_IMAGE_MIMES and mime != "application/octet-stream":
            raise ReceiptError("Файл похож на PNG, но MIME-тип не совпадает.")
        if extension and extension != "png":
            raise ReceiptError("Расширение изображения должно быть .png.")
        return ReceiptInfo(
            receipt_type=ReceiptType.IMAGE,
            mime="image/png",
            extension="png",
            size=size,
        )

    raise ReceiptError("Принимаются только PDF или изображения JPEG/PNG. Проверьте формат файла.")


def _assert_size(size: int, max_bytes: int) -> None:
    if size <= 0:
        raise ReceiptError("Файл пустой.")
    if size > max_bytes:
        raise ReceiptError("Файл слишком большой.")


def _validate_pdf(data: bytes) -> None:
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise ReceiptError("PDF защищён паролем. Сохраните файл без пароля и пришлите снова.")
        _ = len(reader.pages)
    except ReceiptError:
        raise
    except (PdfReadError, ValueError, OSError) as exc:
        raise ReceiptError("PDF-файл повреждён или защищён паролем.") from exc


def _validate_image(data: bytes, *, expected_format: str) -> None:
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format != expected_format:
                raise ReceiptError("Содержимое изображения не совпадает с его форматом.")
            image.verify()
    except ReceiptError:
        raise
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ReceiptError("Изображение повреждено.") from exc
