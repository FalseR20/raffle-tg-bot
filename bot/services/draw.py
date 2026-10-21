"""Manual and optional random winner selection."""

from __future__ import annotations

import secrets
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.enums import RaffleStatus
from bot.db.models import Application, Raffle, Winner
from bot.db.repositories import applications as apps_repo
from bot.db.repositories import audit as audit_repo
from bot.db.repositories import winners as winners_repo
from bot.domain.prizes import choose_ticket, max_prizes_per_ticket
from bot.services.errors import (
    AlreadyProcessedError,
    IntegrityError,
    InvalidStateError,
    NotFoundError,
    ValidationError,
)
from bot.services.permissions import Permissions


def _assert_manage(actor: Permissions) -> None:
    if not actor.can_manage_raffles:
        raise PermissionError("У вас нет прав для записи победителей.")


def _ticket_index(
    applications: list[Application], last_ticket_number: int
) -> dict[int, Application]:
    mapping: dict[int, Application] = {}
    for app in applications:
        if app.first_ticket_number is None or app.last_ticket_number is None:
            continue
        for number in range(app.first_ticket_number, app.last_ticket_number + 1):
            mapping[number] = app
    if len(mapping) != last_ticket_number:
        raise IntegrityError("Нумерация билетов неполная; розыгрыш остановлен.")
    return mapping


async def record_manual_winner(
    session: AsyncSession,
    *,
    actor: Permissions,
    raffle: Raffle,
    place: int,
    ticket_number: int,
    now: datetime,
) -> Winner:
    if not actor.can_manage_raffles:
        from bot.services.errors import PermissionDeniedError

        raise PermissionDeniedError()
    if raffle.status not in {RaffleStatus.FINISHED, RaffleStatus.ARCHIVED}:
        raise InvalidStateError("Записывать победителей можно только после завершения розыгрыша.")
    if not 1 <= place <= raffle.prizes_count:
        raise ValidationError("Место должно быть в пределах количества призов.")
    if not 1 <= ticket_number <= raffle.last_ticket_number:
        raise ValidationError("Такого билета нет в розыгрыше.")

    applications = await apps_repo.all_confirmed(session, raffle.id)
    mapping = _ticket_index(applications, raffle.last_ticket_number)
    application = mapping.get(ticket_number)
    if application is None:
        raise NotFoundError("Не найден владелец билета.")

    winner = await winners_repo.get_by_place(session, raffle.id, place)
    if winner is None:
        winner = await winners_repo.create(
            session,
            raffle_id=raffle.id,
            place=place,
            ticket_number=ticket_number,
            application_id=application.id,
            created_at=now,
        )
    else:
        winner.ticket_number = ticket_number
        winner.application_id = application.id
        await session.flush()

    await audit_repo.log(
        session,
        entity_type="raffle",
        entity_id=raffle.id,
        raffle_id=raffle.id,
        actor_id=actor.user_id,
        action="winner_recorded",
        payload={
            "place": place,
            "ticket_number": ticket_number,
            "application_id": application.id,
            "user_id": application.user_id,
            "mode": "manual",
        },
        at=now,
    )
    return winner


async def run_random_draw(
    session: AsyncSession,
    *,
    actor: Permissions,
    raffle: Raffle,
    now: datetime,
    rng: secrets.SystemRandom | None = None,
) -> list[Winner]:
    if not actor.can_manage_raffles:
        from bot.services.errors import PermissionDeniedError

        raise PermissionDeniedError()
    if raffle.status is not RaffleStatus.FINISHED:
        raise InvalidStateError("Случайный розыгрыш доступен только после завершения.")
    if raffle.last_ticket_number < 1:
        raise InvalidStateError("В розыгрыше нет билетов; розыгрыш невозможен.")
    existing = await winners_repo.list_for_raffle(session, raffle.id)
    if existing:
        raise AlreadyProcessedError("Победители уже записаны.")

    applications = await apps_repo.all_confirmed(session, raffle.id)
    mapping = _ticket_index(applications, raffle.last_ticket_number)
    tickets = sorted(mapping)
    cap = max_prizes_per_ticket(raffle.prizes_count, raffle.last_ticket_number)
    generator = rng or secrets.SystemRandom()

    used: dict[int, int] = {}
    selected: list[tuple[int, int, Application]] = []
    for place in range(1, raffle.prizes_count + 1):
        ticket = choose_ticket(tickets, used, cap, generator)
        used[ticket] = used.get(ticket, 0) + 1
        selected.append((place, ticket, mapping[ticket]))

    winners: list[Winner] = []
    for place, ticket, application in selected:
        winners.append(
            await winners_repo.create(
                session,
                raffle_id=raffle.id,
                place=place,
                ticket_number=ticket,
                application_id=application.id,
                created_at=now,
            )
        )

    await audit_repo.log(
        session,
        entity_type="raffle",
        entity_id=raffle.id,
        raffle_id=raffle.id,
        actor_id=actor.user_id,
        action="random_draw",
        payload={
            "algorithm": "uniform-eligible-ticket",
            "cap_per_ticket": cap,
            "prizes_count": raffle.prizes_count,
            "last_ticket_number": raffle.last_ticket_number,
            "winners": [
                {"place": place, "ticket": ticket, "application_id": app.id}
                for place, ticket, app in selected
            ],
        },
        at=now,
    )
    return winners


async def reset_winners(
    session: AsyncSession, *, actor: Permissions, raffle: Raffle, now: datetime
) -> None:
    from bot.services.errors import PermissionDeniedError

    if not actor.can_manage_raffles:
        raise PermissionDeniedError()
    if raffle.status not in {RaffleStatus.FINISHED, RaffleStatus.ARCHIVED}:
        raise InvalidStateError("Сбросить победителей можно только после завершения.")
    await winners_repo.delete_all(session, raffle.id)
    await audit_repo.log(
        session,
        entity_type="raffle",
        entity_id=raffle.id,
        raffle_id=raffle.id,
        actor_id=actor.user_id,
        action="winners_reset",
        at=now,
    )
