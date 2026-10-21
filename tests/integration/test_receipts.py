"""Receipt validation and RustFS storage integration tests."""

from __future__ import annotations

import io
import socket
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from urllib.parse import urlparse

import pytest
import pytest_asyncio
from PIL import Image
from pypdf import PdfWriter
from reportlab.pdfgen import canvas

from bot.db.enums import ApplicationStatus, ReceiptType
from bot.db.session import session_scope
from bot.services import receipts as receipts_service
from bot.services.errors import ReceiptError, StorageError
from bot.services.receipt_upload import IncomingReceipt, accept_receipt
from bot.services.storage import ReceiptStorage
from tests.helpers import make_application, make_organization, make_raffle, make_user

pytestmark = pytest.mark.integration


def make_jpeg() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (20, 20), "white").save(buffer, format="JPEG")
    return buffer.getvalue()


def make_png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (20, 20), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def make_pdf(text: str = "receipt") -> bytes:
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer)
    pdf.drawString(100, 750, text)
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


class FakeStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.fail_put = False

    async def put(self, key: str, data: bytes, *, content_type: str) -> str:
        if self.fail_put:
            raise StorageError("simulated outage")
        self.objects[key] = data
        return "etag"

    async def get(self, key: str) -> bytes:
        return self.objects[key]

    async def delete(self, key: str) -> None:
        self.objects.pop(key, None)


async def _pending_application(
    session, *, status=ApplicationStatus.AWAITING_RECEIPT, submitted=None
):
    user = await make_user(session, 500)
    org = await make_organization(session)
    raffle = await make_raffle(session, organizations=[org])
    application = await make_application(
        session,
        raffle=raffle,
        user=user,
        organization=org,
        amount=Decimal("5"),
        status=status,
        submitted_at=submitted,
        with_receipt=False,
    )
    return application


async def test_valid_pdf_moves_to_pending_and_stores_object(session_factory):
    async with session_scope(session_factory) as session:
        application = await _pending_application(session)
    storage = FakeStorage()
    now = datetime.now(UTC)
    async with session_scope(session_factory) as session:
        application = await session.get(type(application), application.id)
        result = await accept_receipt(
            session,
            application=application,
            user_id=500,
            incoming=IncomingReceipt(
                file_id="file1",
                file_unique_id="unique1",
                size=len(make_pdf()),
                is_photo=False,
                mime="application/pdf",
                file_name="receipt.pdf",
            ),
            max_bytes=20 * 1024 * 1024,
            download=lambda _: _async_value(make_pdf()),
            storage=storage,
            now=now,
        )
        assert result.application.status is ApplicationStatus.PENDING_REVIEW
        assert result.application.receipt_object_key == "receipts/1/1/unique1.pdf"
        assert result.application.submitted_at is not None
        assert storage.objects[result.object_key].startswith(b"%PDF-")


async def _async_value(value: bytes) -> bytes:
    return value


async def test_storage_failure_keeps_application_unprocessed(session_factory):
    async with session_scope(session_factory) as session:
        application = await _pending_application(session)
    storage = FakeStorage()
    storage.fail_put = True
    async with session_scope(session_factory) as session:
        application = await session.get(type(application), application.id)
        with pytest.raises(StorageError):
            await accept_receipt(
                session,
                application=application,
                user_id=500,
                incoming=IncomingReceipt(
                    file_id="file1",
                    file_unique_id="unique1",
                    size=10,
                    is_photo=False,
                    mime="application/pdf",
                    file_name="receipt.pdf",
                ),
                max_bytes=20 * 1024 * 1024,
                download=lambda _: _async_value(make_pdf()),
                storage=storage,
                now=datetime.now(UTC),
            )
    async with session_factory() as session:
        refreshed = await session.get(type(application), application.id)
        assert refreshed.status is ApplicationStatus.AWAITING_RECEIPT
        assert refreshed.receipt_object_key is None


async def test_duplicate_receipt_is_rejected(session_factory):
    async with session_scope(session_factory) as session:
        first = await _pending_application(session)
        user = await make_user(session, 501)
        org = first.organization
        raffle = first.raffle
        second = await make_application(
            session,
            raffle=raffle,
            user=user,
            organization=org,
            amount=Decimal("5"),
            status=ApplicationStatus.AWAITING_RECEIPT,
            with_receipt=False,
        )
    storage = FakeStorage()
    now = datetime.now(UTC)

    async def upload(application_id: int):
        async with session_scope(session_factory) as session:
            application = await session.get(type(first), application_id)
            return await accept_receipt(
                session,
                application=application,
                user_id=application.user_id,
                incoming=IncomingReceipt(
                    file_id=f"file-{application_id}",
                    file_unique_id="same-receipt",
                    size=10,
                    is_photo=False,
                    mime="application/pdf",
                    file_name="receipt.pdf",
                ),
                max_bytes=20 * 1024 * 1024,
                download=lambda _: _async_value(make_pdf()),
                storage=storage,
                now=now,
            )

    async with session_factory() as session:
        first_user = first.user_id
        second_user = second.user_id
    await upload(first.id)
    async with session_scope(session_factory) as session:
        application = await session.get(type(second), second.id)
        with pytest.raises(ReceiptError):
            await accept_receipt(
                session,
                application=application,
                user_id=second_user,
                incoming=IncomingReceipt(
                    file_id="file2",
                    file_unique_id="same-receipt",
                    size=10,
                    is_photo=False,
                    mime="application/pdf",
                    file_name="receipt.pdf",
                ),
                max_bytes=20 * 1024 * 1024,
                download=lambda _: _async_value(make_pdf()),
                storage=storage,
                now=now,
            )
    del first_user


async def test_revision_preserves_original_submitted_at(session_factory):
    original = datetime.now(UTC) - timedelta(days=1)
    async with session_scope(session_factory) as session:
        application = await _pending_application(
            session, status=ApplicationStatus.REVISION_REQUESTED, submitted=original
        )
        # The original draft is older than the draft TTL. That must not block
        # an already submitted application from uploading a revised receipt.
        application.created_at = datetime.now(UTC) - timedelta(days=2)
    async with session_scope(session_factory) as session:
        application = await session.get(type(application), application.id)
        result = await accept_receipt(
            session,
            application=application,
            user_id=500,
            incoming=IncomingReceipt(
                file_id="new",
                file_unique_id="new-unique",
                size=10,
                is_photo=False,
                mime="application/pdf",
                file_name="receipt.pdf",
            ),
            max_bytes=20 * 1024 * 1024,
            download=lambda _: _async_value(make_pdf()),
            storage=FakeStorage(),
            now=datetime.now(UTC),
        )
        assert result.application.submitted_at == original
        assert result.application.status is ApplicationStatus.PENDING_REVIEW


@pytest.mark.parametrize(
    ("data", "mime", "name", "expected"),
    [
        (make_jpeg(), "image/jpeg", "receipt.jpg", ReceiptType.IMAGE),
        (make_png(), "image/png", "receipt.png", ReceiptType.IMAGE),
        (make_pdf(), "application/pdf", "receipt.pdf", ReceiptType.PDF),
    ],
)
def test_document_types(data, mime, name, expected):
    info = receipts_service.inspect_document(
        data=data,
        file_name=name,
        declared_mime=mime,
        size=len(data),
        max_bytes=20 * 1024 * 1024,
    )
    assert info.receipt_type is expected


def test_fake_and_password_pdf_rejected():
    with pytest.raises(ReceiptError):
        receipts_service.inspect_document(
            data=b"%PDF-1.4 not really a pdf",
            file_name="x.pdf",
            declared_mime="application/pdf",
            size=20,
            max_bytes=100,
        )
    writer = PdfWriter()
    writer.add_blank_page(100, 100)
    writer.encrypt("secret")
    buffer = io.BytesIO()
    writer.write(buffer)
    with pytest.raises(ReceiptError):
        receipts_service.inspect_document(
            data=buffer.getvalue(),
            file_name="x.pdf",
            declared_mime="application/pdf",
            size=len(buffer.getvalue()),
            max_bytes=20 * 1024 * 1024,
        )


def test_fake_image_and_oversized_rejected():
    with pytest.raises(ReceiptError):
        receipts_service.inspect_document(
            data=b"\xff\xd8\xffgarbage",
            file_name="x.jpg",
            declared_mime="image/jpeg",
            size=12,
            max_bytes=100,
        )
    with pytest.raises(ReceiptError):
        receipts_service.inspect_photo(size=1000, max_bytes=10)


def test_object_key_is_sanitized():
    key = receipts_service.build_object_key(
        raffle_id=1,
        application_id=2,
        file_unique_id="../../evil?x",
        extension="jpg",
    )
    assert key == "receipts/1/2/evilx.jpg"


def _rustfs_reachable(endpoint: str) -> bool:
    parsed = urlparse(endpoint)
    try:
        with socket.create_connection((parsed.hostname or "localhost", parsed.port or 9000), 2):
            return True
    except OSError:
        return False


@pytest_asyncio.fixture
async def rustfs(env_settings) -> ReceiptStorage:
    if not _rustfs_reachable(env_settings.rustfs_endpoint):
        pytest.skip("RustFS test endpoint is not reachable")
    storage = ReceiptStorage(
        endpoint=env_settings.rustfs_endpoint,
        access_key=env_settings.rustfs_access_key,
        secret_key=env_settings.rustfs_secret_key,
        bucket=env_settings.rustfs_bucket,
        region=env_settings.rustfs_region,
        use_ssl=env_settings.rustfs_use_ssl,
        force_path_style=env_settings.rustfs_force_path_style,
    )
    await storage.ensure_bucket()
    return storage


async def test_rustfs_put_get_delete(rustfs):
    key = f"tests/{datetime.now(UTC).timestamp()}.pdf"
    data = make_pdf("object storage")
    etag = await rustfs.put(key, data, content_type="application/pdf")
    assert etag
    assert await rustfs.exists(key)
    assert await rustfs.get(key) == data
    await rustfs.delete(key)
    assert not await rustfs.exists(key)


async def test_real_storage_upload_flow(session_factory, rustfs):
    async with session_scope(session_factory) as session:
        application = await _pending_application(session)
    data = make_pdf("real storage")
    unique = f"real-{datetime.now(UTC).timestamp()}"
    async with session_scope(session_factory) as session:
        application = await session.get(type(application), application.id)
        result = await accept_receipt(
            session,
            application=application,
            user_id=500,
            incoming=IncomingReceipt(
                file_id="file",
                file_unique_id=unique,
                size=len(data),
                is_photo=False,
                mime="application/pdf",
                file_name="receipt.pdf",
            ),
            max_bytes=20 * 1024 * 1024,
            download=lambda _: _async_value(data),
            storage=rustfs,
            now=datetime.now(UTC),
        )
    stored = await rustfs.get(result.object_key)
    assert stored == data
    await rustfs.delete(result.object_key)


async def test_photo_upload_uses_jpeg_and_largest_file(session_factory):
    async with session_scope(session_factory) as session:
        application = await _pending_application(session)
    data = make_jpeg()
    async with session_scope(session_factory) as session:
        application = await session.get(type(application), application.id)
        result = await accept_receipt(
            session,
            application=application,
            user_id=500,
            incoming=IncomingReceipt(
                file_id="photo-id",
                file_unique_id="photo-unique",
                size=len(data),
                is_photo=True,
            ),
            max_bytes=20 * 1024 * 1024,
            download=lambda _: _async_value(data),
            storage=FakeStorage(),
            now=datetime.now(UTC),
        )
        assert result.receipt_type is ReceiptType.PHOTO
        assert result.object_key.endswith(".jpg")
        assert result.application.receipt_mime == "image/jpeg"


async def test_awaiting_receipt_cannot_be_uploaded_after_close(session_factory):
    from bot.db.enums import RaffleStatus
    from bot.services.errors import RaffleNotAcceptingError

    async with session_scope(session_factory) as session:
        application = await _pending_application(session)
        raffle = application.raffle
        raffle.status = RaffleStatus.CLOSED
    data = make_pdf()
    async with session_scope(session_factory) as session:
        application = await session.get(type(application), application.id)
        with pytest.raises(RaffleNotAcceptingError):
            await accept_receipt(
                session,
                application=application,
                user_id=500,
                incoming=IncomingReceipt(
                    file_id="file",
                    file_unique_id="closed-unique",
                    size=len(data),
                    is_photo=False,
                    mime="application/pdf",
                    file_name="receipt.pdf",
                ),
                max_bytes=20 * 1024 * 1024,
                download=lambda _: _async_value(data),
                storage=FakeStorage(),
                now=datetime.now(UTC),
            )
