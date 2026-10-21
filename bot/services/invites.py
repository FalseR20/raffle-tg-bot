"""One-time invite service for organizers and organization representatives."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.enums import InviteRole, RoleName
from bot.db.models import Invite
from bot.db.repositories import audit as audit_repo
from bot.db.repositories import invites as invites_repo
from bot.db.repositories import organizations as orgs_repo
from bot.db.repositories import roles as roles_repo
from bot.services.errors import AlreadyProcessedError, NotFoundError, PermissionDeniedError
from bot.services.permissions import Permissions

INVITE_TTL_DAYS = 7


@dataclass(frozen=True)
class CreatedInvite:
    invite: Invite
    token: str
    url: str


@dataclass(frozen=True)
class RedeemedInvite:
    invite: Invite
    role: RoleName
    organization_id: int | None


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _validation_error(message: str) -> AlreadyProcessedError:
    return AlreadyProcessedError(message)


async def create_invite(
    session: AsyncSession,
    *,
    actor: Permissions,
    role: InviteRole,
    organization_id: int | None,
    bot_username: str,
    now: datetime,
    ttl_days: int = INVITE_TTL_DAYS,
) -> CreatedInvite:
    if role is InviteRole.ORGANIZER:
        if not actor.can_manage_organizers:
            raise PermissionDeniedError()
    elif role is InviteRole.ORG_REP:
        if not actor.can_manage_org_representatives:
            raise PermissionDeniedError()
        if organization_id is None:
            raise NotFoundError("Для представителя организации нужна организация.")
        if await orgs_repo.get(session, organization_id) is None:
            raise NotFoundError("Организация не найдена.")
    else:  # pragma: no cover
        raise PermissionDeniedError(
            "Приглашение можно создать только для организатора или представителя."
        )

    token = secrets.token_urlsafe(24)
    invite = await invites_repo.create(
        session,
        token_hash=hash_token(token),
        role=role,
        organization_id=organization_id,
        created_by=actor.user_id,
        expires_at=now + timedelta(days=ttl_days),
        created_at=now,
    )
    await audit_repo.log(
        session,
        entity_type="role",
        entity_id=f"invite:{invite.id}",
        actor_id=actor.user_id,
        action="invite_created",
        payload={
            "role": role.value,
            "organization_id": organization_id,
            "expires_at": invite.expires_at.isoformat(),
        },
        at=now,
    )
    return CreatedInvite(
        invite=invite,
        token=token,
        url=f"https://t.me/{bot_username}?start=inv_{token}",
    )


async def redeem_invite(
    session: AsyncSession,
    *,
    token: str,
    user_id: int,
    now: datetime,
) -> RedeemedInvite:
    token_hash = hash_token(token)
    invite = await invites_repo.get_by_hash_locked(session, token_hash)
    if invite is None:
        raise _validation_error("Приглашение не найдено или ссылка неверна.")
    if not hmac.compare_digest(invite.token_hash, token_hash):  # pragma: no cover - defensive
        raise _validation_error("Приглашение не найдено.")
    if invite.revoked_at is not None:
        raise _validation_error("Приглашение отозвано.")
    if invite.used_at is not None:
        raise _validation_error("Приглашение уже использовано.")
    if invite.expires_at <= now:
        raise _validation_error("Срок действия приглашения истёк.")

    role = RoleName.ORGANIZER if invite.role is InviteRole.ORGANIZER else RoleName.ORG_REP

    if invite.organization_id is not None:
        organization = await orgs_repo.get(session, invite.organization_id)
        if organization is None or not organization.is_active:
            raise _validation_error("Организация приглашения недоступна.")

    already = await roles_repo.has_role(
        session, user_id, role, organization_id=invite.organization_id
    )
    if not already:
        await roles_repo.grant(
            session,
            user_id=user_id,
            role=role,
            organization_id=invite.organization_id,
            granted_by=invite.created_by,
            at=now,
        )
        await audit_repo.log(
            session,
            entity_type="role",
            entity_id=f"{user_id}:{role.value}:{invite.organization_id or 0}",
            actor_id=invite.created_by,
            action="role_granted",
            payload={
                "user_id": user_id,
                "role": role.value,
                "organization_id": invite.organization_id,
                "source": "invite",
                "invite_id": invite.id,
            },
            at=now,
        )

    invite.used_by = user_id
    invite.used_at = now
    await session.flush()

    await audit_repo.log(
        session,
        entity_type="role",
        entity_id=f"invite:{invite.id}",
        actor_id=user_id,
        action="invite_redeemed",
        payload={
            "role": role.value,
            "organization_id": invite.organization_id,
            "already_had_role": already,
        },
        at=now,
    )
    return RedeemedInvite(invite=invite, role=role, organization_id=invite.organization_id)


async def revoke_invite(
    session: AsyncSession,
    *,
    actor: Permissions,
    invite_id: int,
    now: datetime,
) -> None:
    if not actor.can_manage_org_representatives:
        raise PermissionDeniedError()
    invite = await invites_repo.get(session, invite_id)
    if invite is None:
        raise NotFoundError("Приглашение не найдено.")
    if invite.role is InviteRole.ORGANIZER and not actor.can_manage_organizers:
        raise PermissionDeniedError()
    if invite.used_at is not None or invite.revoked_at is not None:
        raise AlreadyProcessedError("Приглашение уже использовано или отозвано.")
    invite.revoked_at = now
    invite.revoked_by = actor.user_id
    await session.flush()
    await audit_repo.log(
        session,
        entity_type="role",
        entity_id=f"invite:{invite.id}",
        actor_id=actor.user_id,
        action="invite_revoked",
        at=now,
    )
