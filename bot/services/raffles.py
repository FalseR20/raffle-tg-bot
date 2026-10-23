"""Raffle lifecycle: creation, activation, stop/resume, finish and archive."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.enums import ApplicationStatus, RaffleStatus
from bot.db.models import Application, AuditLog, Organization, Raffle, Winner
from bot.db.repositories import applications as apps_repo
from bot.db.repositories import audit as audit_repo
from bot.db.repositories import organizations as orgs_repo
from bot.db.repositories import raffles as raffles_repo
from bot.logging import get_logger
from bot.services.errors import (
    InvalidStateError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from bot.services.permissions import Permissions
from bot.services.storage import ReceiptStorage
from bot.utils.slug import slugify, unique_slug
from bot.utils.url import normalize_optional_http_url

logger = get_logger(__name__)

OPEN_REVIEW_STATUSES = (ApplicationStatus.PENDING_REVIEW, ApplicationStatus.REVISION_REQUESTED)
UNFINISHED_STATUSES = (
    ApplicationStatus.DRAFT,
    ApplicationStatus.AWAITING_RECEIPT,
)


@dataclass(frozen=True)
class RaffleInput:
    title: str
    description: str
    ticket_price: Decimal
    min_amount: Decimal
    prizes_count: int
    start_at: datetime
    end_at: datetime
    channel_url: str
    channel_title: str
    rules_text: str | None = None


@dataclass(frozen=True)
class ActivationProblem:
    code: str
    message: str


@dataclass(frozen=True)
class FinishResult:
    expired_applications: list[Application]

    @property
    def expired_count(self) -> int:
        return len(self.expired_applications)


@dataclass(frozen=True)
class ArchivedRaffleDeletion:
    """Identity of a removed archive. Receipt keys are deleted after commit."""

    raffle_id: int
    title: str
    slug: str
    receipt_object_keys: tuple[str, ...]


def _assert_manage(actor: Permissions) -> None:
    if not actor.can_manage_raffles:
        raise PermissionDeniedError()


def validate_input(data: RaffleInput) -> None:
    if not data.title.strip():
        raise ValidationError("Укажите название розыгрыша.")
    if data.ticket_price <= 0:
        raise ValidationError("Цена билета должна быть больше нуля.")
    if data.min_amount < data.ticket_price:
        raise ValidationError("Минимальная сумма не может быть меньше цены билета.")
    if data.prizes_count < 1:
        raise ValidationError("Количество призов должно быть не меньше одного.")
    if data.end_at <= data.start_at:
        raise ValidationError("Дата окончания должна быть позже даты начала.")
    if data.channel_url:
        try:
            normalize_optional_http_url(data.channel_url)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc


async def _unique_raffle_slug(session: AsyncSession, title: str) -> str:
    existing = await raffles_repo.list_all(session, limit=1000)
    taken = {raffle.slug for raffle in existing}
    return unique_slug(slugify(title), taken)


async def create_raffle(
    session: AsyncSession,
    *,
    actor: Permissions,
    data: RaffleInput,
    organization_ids: list[int],
    now: datetime,
) -> Raffle:
    _assert_manage(actor)
    validate_input(data)
    if not organization_ids:
        raise ValidationError("Выберите хотя бы одну организацию.")
    for organization_id in organization_ids:
        if await orgs_repo.get(session, organization_id) is None:
            raise NotFoundError("Организация не найдена.")

    raffle = await raffles_repo.create(
        session,
        slug=await _unique_raffle_slug(session, data.title),
        title=data.title.strip(),
        description=data.description,
        rules_text=data.rules_text,
        ticket_price=data.ticket_price,
        min_amount=data.min_amount,
        prizes_count=data.prizes_count,
        currency="BYN",
        start_at=data.start_at,
        end_at=data.end_at,
        status=RaffleStatus.DRAFT,
        channel_url=normalize_optional_http_url(data.channel_url) or "",
        channel_title=data.channel_title,
        last_ticket_number=0,
        created_by=actor.user_id,
        created_at=now,
    )
    await raffles_repo.set_organizations(session, raffle.id, organization_ids)
    await audit_repo.log(
        session,
        entity_type="raffle",
        entity_id=raffle.id,
        raffle_id=raffle.id,
        actor_id=actor.user_id,
        action="raffle_created",
        new_status=RaffleStatus.DRAFT.value,
        payload={"title": raffle.title, "organization_ids": organization_ids},
        at=now,
    )
    return raffle


async def update_raffle(
    session: AsyncSession,
    *,
    actor: Permissions,
    raffle: Raffle,
    data: RaffleInput,
) -> Raffle:
    _assert_manage(actor)
    validate_input(data)
    if raffle.status in {RaffleStatus.FINISHED, RaffleStatus.ARCHIVED}:
        raise InvalidStateError("Завершённый розыгрыш нельзя редактировать.")

    locked_changed = (
        raffle.ticket_price != data.ticket_price
        or raffle.min_amount != data.min_amount
        or raffle.prizes_count != data.prizes_count
        or raffle.start_at != data.start_at
    )
    if raffle.status.is_current and locked_changed:
        raise InvalidStateError(
            "После активации нельзя менять цену билета, минимальную сумму, призы и дату начала."
        )

    raffle.title = data.title.strip()
    raffle.description = data.description
    raffle.rules_text = data.rules_text
    raffle.ticket_price = data.ticket_price
    raffle.min_amount = data.min_amount
    raffle.prizes_count = data.prizes_count
    raffle.start_at = data.start_at
    raffle.end_at = data.end_at
    raffle.channel_url = normalize_optional_http_url(data.channel_url) or ""
    raffle.channel_title = data.channel_title
    await session.flush()
    return raffle


async def activation_problems(session: AsyncSession, raffle: Raffle) -> list[ActivationProblem]:
    problems: list[ActivationProblem] = []
    current = await raffles_repo.get_current(session)
    if current is not None and current.id != raffle.id:
        problems.append(
            ActivationProblem(
                "current_exists",
                f"Уже есть текущий розыгрыш «{current.title}». Сначала завершите его.",
            )
        )
    if raffle.ticket_price <= 0:
        problems.append(ActivationProblem("ticket_price", "Цена билета должна быть больше нуля."))
    if raffle.min_amount < raffle.ticket_price:
        problems.append(ActivationProblem("min_amount", "Минимальная сумма меньше цены билета."))
    if raffle.prizes_count < 1:
        problems.append(ActivationProblem("prizes", "Количество призов меньше одного."))
    if raffle.end_at <= raffle.start_at:
        problems.append(ActivationProblem("dates", "Дата окончания не позже даты начала."))

    organizations = await raffles_repo.enabled_organizations(session, raffle.id)
    if not organizations:
        problems.append(ActivationProblem("organizations", "Не выбрано ни одной организации."))
    for organization in organizations:
        if not organization.name.strip():
            problems.append(
                ActivationProblem("org_name", f"У организации {organization.id} нет названия.")
            )
        if not organization.review_chat_id:
            problems.append(
                ActivationProblem(
                    "org_chat", f"У организации «{organization.name}» не указан чат проверки."
                )
            )
        if not await orgs_repo.has_active_payment_methods(session, organization.id):
            problems.append(
                ActivationProblem(
                    "org_methods",
                    f"У организации «{organization.name}» нет активного способа оплаты.",
                )
            )
    return problems


async def activate(
    session: AsyncSession, *, actor: Permissions, raffle: Raffle, now: datetime
) -> Raffle:
    _assert_manage(actor)
    if raffle.status not in {RaffleStatus.DRAFT, RaffleStatus.SCHEDULED}:
        raise InvalidStateError("Активировать можно только черновик или запланированный розыгрыш.")
    problems = await activation_problems(session, raffle)
    if problems:
        raise ValidationError(problems[0].message)

    old_status = raffle.status
    raffle.status = RaffleStatus.ACTIVE if raffle.start_at <= now else RaffleStatus.SCHEDULED
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise ValidationError("Уже есть активный розыгрыш.") from exc
    await audit_repo.log(
        session,
        entity_type="raffle",
        entity_id=raffle.id,
        raffle_id=raffle.id,
        actor_id=actor.user_id,
        action="raffle_activated",
        old_status=old_status.value,
        new_status=raffle.status.value,
        at=now,
    )
    return raffle


async def stop_acceptance(
    session: AsyncSession, *, actor: Permissions, raffle: Raffle, now: datetime
) -> Raffle:
    _assert_manage(actor)
    if raffle.status is not RaffleStatus.ACTIVE:
        raise InvalidStateError("Остановить приём можно только у активного розыгрыша.")
    raffle.status = RaffleStatus.CLOSED
    await session.flush()
    from bot.services.applications import expire_raffle_drafts

    await expire_raffle_drafts(session, raffle_id=raffle.id, now=now, reason="acceptance_stopped")
    await audit_repo.log(
        session,
        entity_type="raffle",
        entity_id=raffle.id,
        raffle_id=raffle.id,
        actor_id=actor.user_id,
        action="raffle_closed",
        old_status=RaffleStatus.ACTIVE.value,
        new_status=RaffleStatus.CLOSED.value,
        at=now,
    )
    return raffle


async def close_due(session: AsyncSession, *, raffle: Raffle, now: datetime) -> bool:
    """Close an active raffle whose deadline has passed. Scheduler-safe."""
    if raffle.status is not RaffleStatus.ACTIVE or raffle.end_at > now:
        return False
    raffle.status = RaffleStatus.CLOSED
    await session.flush()
    from bot.services.applications import expire_raffle_drafts

    await expire_raffle_drafts(session, raffle_id=raffle.id, now=now, reason="deadline")
    await audit_repo.log(
        session,
        entity_type="raffle",
        entity_id=raffle.id,
        raffle_id=raffle.id,
        actor_id=None,
        action="raffle_closed",
        old_status=RaffleStatus.ACTIVE.value,
        new_status=RaffleStatus.CLOSED.value,
        payload={"reason": "deadline"},
        at=now,
    )
    return True


async def resume(
    session: AsyncSession, *, actor: Permissions, raffle: Raffle, now: datetime
) -> Raffle:
    _assert_manage(actor)
    if raffle.status is not RaffleStatus.CLOSED:
        raise InvalidStateError("Возобновить приём можно только у закрытого розыгрыша.")
    if raffle.end_at <= now:
        raise InvalidStateError("Сначала продлите дату окончания розыгрыша.")
    current = await raffles_repo.get_current(session)
    if current is not None and current.id != raffle.id:
        raise InvalidStateError("Уже есть другой текущий розыгрыш.")
    raffle.status = RaffleStatus.ACTIVE
    await session.flush()
    await audit_repo.log(
        session,
        entity_type="raffle",
        entity_id=raffle.id,
        raffle_id=raffle.id,
        actor_id=actor.user_id,
        action="raffle_resumed",
        old_status=RaffleStatus.CLOSED.value,
        new_status=RaffleStatus.ACTIVE.value,
        at=now,
    )
    return raffle


async def extend_end_at(
    session: AsyncSession,
    *,
    actor: Permissions,
    raffle: Raffle,
    new_end_at: datetime,
    now: datetime,
) -> Raffle:
    _assert_manage(actor)
    if raffle.status.is_finished:
        raise InvalidStateError("Завершённый розыгрыш нельзя продлить.")
    if new_end_at <= raffle.start_at:
        raise ValidationError("Дата окончания должна быть позже даты начала.")
    old_status = raffle.status
    raffle.end_at = new_end_at
    if raffle.status is RaffleStatus.CLOSED and new_end_at > now:
        raffle.status = RaffleStatus.ACTIVE
    await session.flush()
    await audit_repo.log(
        session,
        entity_type="raffle",
        entity_id=raffle.id,
        raffle_id=raffle.id,
        actor_id=actor.user_id,
        action="raffle_extended",
        old_status=old_status.value,
        new_status=raffle.status.value,
        payload={"new_end_at": new_end_at.isoformat()},
        at=now,
    )
    return raffle


async def finish(
    session: AsyncSession,
    *,
    actor: Permissions,
    raffle: Raffle,
    expire_pending: bool,
    now: datetime,
) -> FinishResult:
    _assert_manage(actor)

    # Lock applications before the raffle, matching the review/void lock order.
    # This prevents a concurrent rejection or revision from changing an
    # application after it has been expired by finish.
    pending = await apps_repo.pending_for_raffle(session, raffle.id, for_update=True)
    drafts = await apps_repo.drafts_for_raffle(session, raffle.id, for_update=True)

    locked_raffle = await raffles_repo.lock(session, raffle.id)
    if locked_raffle is None:
        raise NotFoundError("Розыгрыш не найден.")
    raffle = locked_raffle
    if raffle.status is RaffleStatus.ACTIVE:
        raise InvalidStateError("Сначала остановите приём заявок.")
    if raffle.status is not RaffleStatus.CLOSED:
        raise InvalidStateError("Завершить можно только розыгрыш с остановленным приёмом.")
    if pending and not expire_pending:
        raise InvalidStateError(
            f"Осталось {len(pending)} заявок на проверке. Обработайте их или явно переведите в "
            "«истекла»."
        )

    expired: list[Application] = []
    for application in pending:
        old = application.status
        application.status = ApplicationStatus.EXPIRED
        expired.append(application)
        await audit_repo.log(
            session,
            entity_type="application",
            entity_id=application.id,
            application_id=application.id,
            raffle_id=raffle.id,
            actor_id=actor.user_id,
            action="application_expired",
            old_status=old.value,
            new_status=ApplicationStatus.EXPIRED.value,
            payload={"reason": "raffle_finished"},
            at=now,
        )

    for application in drafts:
        old = application.status
        application.status = ApplicationStatus.EXPIRED
        expired.append(application)
        await audit_repo.log(
            session,
            entity_type="application",
            entity_id=application.id,
            application_id=application.id,
            raffle_id=raffle.id,
            actor_id=actor.user_id,
            action="application_expired",
            old_status=old.value,
            new_status=ApplicationStatus.EXPIRED.value,
            payload={"reason": "raffle_finished"},
            at=now,
        )

    # A draft may have been inserted between the first query and the raffle
    # lock. Re-check after the lock so it cannot survive in a finished raffle.
    late_drafts = await apps_repo.drafts_for_raffle(session, raffle.id, for_update=True)
    for application in late_drafts:
        old = application.status
        application.status = ApplicationStatus.EXPIRED
        expired.append(application)
        await audit_repo.log(
            session,
            entity_type="application",
            entity_id=application.id,
            application_id=application.id,
            raffle_id=raffle.id,
            actor_id=actor.user_id,
            action="application_expired",
            old_status=old.value,
            new_status=ApplicationStatus.EXPIRED.value,
            payload={"reason": "raffle_finished"},
            at=now,
        )

    raffle.status = RaffleStatus.FINISHED
    raffle.finished_at = now
    await session.flush()
    await audit_repo.log(
        session,
        entity_type="raffle",
        entity_id=raffle.id,
        raffle_id=raffle.id,
        actor_id=actor.user_id,
        action="raffle_finished",
        old_status=RaffleStatus.CLOSED.value,
        new_status=RaffleStatus.FINISHED.value,
        payload={"expired_count": len(expired), "last_ticket_number": raffle.last_ticket_number},
        at=now,
    )
    return FinishResult(expired_applications=expired)


async def archive(
    session: AsyncSession, *, actor: Permissions, raffle: Raffle, now: datetime
) -> Raffle:
    _assert_manage(actor)
    if raffle.status is not RaffleStatus.FINISHED:
        raise InvalidStateError("В архив можно отправить только завершённый розыгрыш.")
    raffle.status = RaffleStatus.ARCHIVED
    await session.flush()
    await audit_repo.log(
        session,
        entity_type="raffle",
        entity_id=raffle.id,
        raffle_id=raffle.id,
        actor_id=actor.user_id,
        action="raffle_archived",
        old_status=RaffleStatus.FINISHED.value,
        new_status=RaffleStatus.ARCHIVED.value,
        at=now,
    )
    return raffle


async def return_to_draft(
    session: AsyncSession, *, actor: Permissions, raffle: Raffle, now: datetime
) -> Raffle:
    _assert_manage(actor)
    if raffle.status not in {RaffleStatus.ACTIVE, RaffleStatus.CLOSED}:
        raise InvalidStateError("Вернуть в черновик можно только текущий розыгрыш.")
    applications = await raffles_repo.count_applications(session, raffle.id)
    if applications:
        raise InvalidStateError("Нельзя вернуть в черновик розыгрыш, у которого есть заявки.")
    old_status = raffle.status
    raffle.status = RaffleStatus.DRAFT
    await session.flush()
    await audit_repo.log(
        session,
        entity_type="raffle",
        entity_id=raffle.id,
        raffle_id=raffle.id,
        actor_id=actor.user_id,
        action="raffle_returned_to_draft",
        old_status=old_status.value,
        new_status=RaffleStatus.DRAFT.value,
        at=now,
    )
    return raffle


async def delete_draft(
    session: AsyncSession, *, actor: Permissions, raffle: Raffle, now: datetime
) -> None:
    _assert_manage(actor)
    if raffle.status is not RaffleStatus.DRAFT:
        raise InvalidStateError("Удалять можно только черновик.")
    if await raffles_repo.count_applications(session, raffle.id):
        raise InvalidStateError("Нельзя удалить черновик с заявками.")
    await audit_repo.log(
        session,
        entity_type="raffle",
        entity_id=raffle.id,
        raffle_id=raffle.id,
        actor_id=actor.user_id,
        action="raffle_deleted",
        old_status=raffle.status.value,
        payload={"title": raffle.title},
        at=now,
    )
    await session.delete(raffle)
    await session.flush()


def _expunge_deleted_raffle(session: AsyncSession, raffle_id: int) -> None:
    """Drop ORM instances whose rows were removed in this transaction.

    The audit row stays: its foreign keys are cleared by the database, and the
    in-memory entry is the record of the deletion.
    """
    for obj in list(session.identity_map.values()):
        if isinstance(obj, AuditLog):
            continue
        owns_raffle = isinstance(obj, Raffle) and obj.id == raffle_id
        belongs_to_raffle = getattr(obj, "raffle_id", None) == raffle_id
        if owns_raffle or belongs_to_raffle:
            session.expunge(obj)


async def delete_archived(
    session: AsyncSession, *, actor: Permissions, raffle: Raffle, now: datetime
) -> ArchivedRaffleDeletion:
    """Delete an archived raffle and its dependent rows.

    The caller commits this transaction before deleting receipt objects. Users,
    organizations and the audit log are kept. Receipt keys are returned so the
    caller can remove the files after a successful commit.
    """
    _assert_manage(actor)
    locked = await raffles_repo.lock(session, raffle.id)
    if locked is None:
        raise NotFoundError("Розыгрыш не найден.")
    if locked.status is not RaffleStatus.ARCHIVED:
        raise InvalidStateError("Удалять можно только архивный розыгрыш.")

    raffle_id = locked.id
    title = locked.title
    slug = locked.slug
    last_ticket_number = locked.last_ticket_number

    await session.execute(
        select(Application.id).where(Application.raffle_id == raffle_id).with_for_update()
    )
    await session.execute(select(Winner.id).where(Winner.raffle_id == raffle_id).with_for_update())
    key_rows = await session.execute(
        select(Application.receipt_object_key).where(
            Application.raffle_id == raffle_id,
            Application.receipt_object_key.is_not(None),
        )
    )
    receipt_keys = tuple(key for key in key_rows.scalars().all() if key)
    applications = await raffles_repo.count_applications(session, raffle_id)
    winners = int(
        (
            await session.execute(
                select(func.count()).select_from(Winner).where(Winner.raffle_id == raffle_id)
            )
        ).scalar_one()
    )

    try:
        await session.execute(delete(Winner).where(Winner.raffle_id == raffle_id))
        await session.execute(delete(Application).where(Application.raffle_id == raffle_id))
        await audit_repo.log(
            session,
            entity_type="raffle",
            entity_id=raffle_id,
            raffle_id=raffle_id,
            actor_id=actor.user_id,
            action="raffle_deleted",
            old_status=RaffleStatus.ARCHIVED.value,
            payload={
                "title": title,
                "slug": slug,
                "raffle_id": raffle_id,
                "applications": applications,
                "winners": winners,
                "last_ticket_number": last_ticket_number,
                "receipt_object_keys": list(receipt_keys),
            },
            at=now,
        )
        await session.execute(delete(Raffle).where(Raffle.id == raffle_id))
    except IntegrityError as exc:
        raise InvalidStateError("Не удалось удалить розыгрыш. Попробуйте ещё раз.") from exc

    _expunge_deleted_raffle(session, raffle_id)
    return ArchivedRaffleDeletion(
        raffle_id=raffle_id,
        title=title,
        slug=slug,
        receipt_object_keys=receipt_keys,
    )


async def purge_archived_receipts(
    session: AsyncSession,
    storage: ReceiptStorage,
    *,
    keys: tuple[str, ...],
    raffle_id: int,
    actor_id: int | None,
    now: datetime,
) -> tuple[str, ...]:
    """Delete receipt objects after the archive row has been committed.

    Returns keys that could not be removed. Those failures are written to the
    audit log and do not restore the raffle.
    """
    failed: list[str] = []
    for key in keys:
        try:
            await storage.delete(key)
        except Exception as exc:
            logger.warning("Could not delete receipt object %s: %s", key, exc)
            failed.append(key)
    if failed:
        await audit_repo.log(
            session,
            entity_type="raffle",
            entity_id=raffle_id,
            actor_id=actor_id,
            action="receipt_delete_failed",
            payload={
                "raffle_id": raffle_id,
                "object_keys": failed,
                "reason": "raffle_deleted",
                "error": "storage delete failed",
            },
            at=now,
        )
    return tuple(failed)


async def duplicate(
    session: AsyncSession, *, actor: Permissions, raffle: Raffle, now: datetime
) -> Raffle:
    _assert_manage(actor)
    organization_ids = [entry.organization_id for entry in raffle.organizations if entry.is_enabled]
    data = RaffleInput(
        title=f"{raffle.title} (копия)",
        description=raffle.description,
        ticket_price=raffle.ticket_price,
        min_amount=raffle.min_amount,
        prizes_count=raffle.prizes_count,
        start_at=now + timedelta(days=1),
        end_at=now + timedelta(days=31),
        channel_url=raffle.channel_url,
        channel_title=raffle.channel_title,
        rules_text=raffle.rules_text,
    )
    return await create_raffle(
        session,
        actor=actor,
        data=data,
        organization_ids=organization_ids,
        now=now,
    )


async def process_schedule(
    session: AsyncSession, *, now: datetime
) -> tuple[list[Raffle], list[Raffle], list[tuple[Raffle, str]]]:
    """Apply scheduled starts and due deadlines.

    Returns ``(activated, closed, blocked)`` for notifications. A scheduled
    raffle that cannot start because another raffle is current or because an
    activation check fails stays scheduled and is reported as blocked.
    """
    activated: list[Raffle] = []
    closed: list[Raffle] = []
    blocked: list[tuple[Raffle, str]] = []

    current = await raffles_repo.get_current(session)
    for raffle in await raffles_repo.scheduled_due(session):
        if raffle.start_at > now:
            continue
        if current is not None and current.id != raffle.id:
            blocked.append((raffle, "Уже есть текущий розыгрыш."))
            continue
        problems = await activation_problems(session, raffle)
        if problems:
            blocked.append((raffle, problems[0].message))
            continue
        raffle.status = RaffleStatus.ACTIVE
        await session.flush()
        await audit_repo.log(
            session,
            entity_type="raffle",
            entity_id=raffle.id,
            raffle_id=raffle.id,
            actor_id=None,
            action="raffle_activated",
            old_status=RaffleStatus.SCHEDULED.value,
            new_status=RaffleStatus.ACTIVE.value,
            payload={"reason": "schedule"},
            at=now,
        )
        activated.append(raffle)
        current = raffle

    for raffle in await raffles_repo.list_all(session, status=RaffleStatus.ACTIVE, limit=1000):
        if await close_due(session, raffle=raffle, now=now):
            closed.append(raffle)
    return activated, closed, blocked


def is_accepting(raffle: Raffle | None, now: datetime) -> bool:
    return bool(raffle is not None and raffle.status is RaffleStatus.ACTIVE and raffle.end_at > now)


def organizations_of(raffle: Raffle) -> list[Organization]:
    return [entry.organization for entry in raffle.organizations if entry.is_enabled]
