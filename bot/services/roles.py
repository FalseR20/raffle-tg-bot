"""Role management service.

The environment ``ADMIN_IDS`` list is the source of truth for admin roles.
Organizers are assigned only by admins; org representatives may be assigned by
admins and organizers. Every change is audited.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.enums import RoleName
from bot.db.models import Role
from bot.db.repositories import audit as audit_repo
from bot.db.repositories import organizations as orgs_repo
from bot.db.repositories import roles as roles_repo
from bot.db.repositories import users as users_repo
from bot.services.errors import (
    AlreadyProcessedError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from bot.services.permissions import Permissions


async def sync_admin_roles(
    session: AsyncSession, admin_ids: frozenset[int], *, now: datetime
) -> tuple[list[int], list[int]]:
    """Add missing admin roles and revoke admin roles no longer in ``ADMIN_IDS``.

    Returns ``(granted, revoked)`` user IDs. Other roles of the affected users
    are preserved.
    """
    if not admin_ids:
        raise ValidationError("ADMIN_IDS must not be empty")

    active_admins = await roles_repo.active_holders(session, RoleName.ADMIN)
    current = {role.user_id for role in active_admins}
    granted: list[int] = []
    revoked: list[int] = []

    for user_id in sorted(admin_ids - current):
        await users_repo.upsert(
            session,
            user_id=user_id,
            username=None,
            first_name="",
            last_name=None,
            language_code=None,
            seen_at=now,
        )
        await roles_repo.grant(
            session,
            user_id=user_id,
            role=RoleName.ADMIN,
            organization_id=None,
            granted_by=None,
            at=now,
        )
        await audit_repo.log(
            session,
            entity_type="role",
            entity_id=f"{user_id}:admin",
            actor_id=None,
            action="role_granted",
            payload={"user_id": user_id, "role": RoleName.ADMIN.value, "source": "env"},
            at=now,
        )
        granted.append(user_id)

    for user_id in sorted(current - admin_ids):
        await roles_repo.revoke(
            session,
            user_id=user_id,
            role=RoleName.ADMIN,
            organization_id=None,
            revoked_by=0,
            at=now,
        )
        await audit_repo.log(
            session,
            entity_type="role",
            entity_id=f"{user_id}:admin",
            actor_id=None,
            action="role_revoked",
            payload={"user_id": user_id, "role": RoleName.ADMIN.value, "source": "env"},
            at=now,
        )
        revoked.append(user_id)

    return granted, revoked


async def assign_role(
    session: AsyncSession,
    *,
    actor: Permissions,
    target_user_id: int,
    role: RoleName,
    organization_id: int | None,
    now: datetime,
) -> Role:
    if role is RoleName.ADMIN:
        raise PermissionDeniedError(
            "Роль администратора задаётся только через ADMIN_IDS в настройках окружения."
        )

    if await users_repo.get(session, target_user_id) is None:
        raise NotFoundError("Пользователь ещё не запускал бота.")

    if role is RoleName.ORGANIZER:
        if not actor.can_manage_organizers:
            raise PermissionDeniedError()
    elif role is RoleName.ORG_REP:
        if not actor.can_manage_org_representatives:
            raise PermissionDeniedError()
        if organization_id is None:
            raise ValidationError("Для представителя организации нужна организация.")
        organization = await orgs_repo.get(session, organization_id)
        if organization is None:
            raise NotFoundError("Организация не найдена.")
    else:  # pragma: no cover - exhaustive
        raise ValidationError("Неизвестная роль.")

    existing = await roles_repo.active_for_user(session, target_user_id)
    for entry in existing:
        if entry.role is role and entry.organization_id == organization_id:
            raise AlreadyProcessedError("У пользователя уже есть эта роль.")

    granted = await roles_repo.grant(
        session,
        user_id=target_user_id,
        role=role,
        organization_id=organization_id,
        granted_by=actor.user_id,
        at=now,
    )
    await audit_repo.log(
        session,
        entity_type="role",
        entity_id=f"{target_user_id}:{role.value}:{organization_id or 0}",
        actor_id=actor.user_id,
        action="role_granted",
        payload={
            "user_id": target_user_id,
            "role": role.value,
            "organization_id": organization_id,
        },
        at=now,
    )
    return granted


async def revoke_role(
    session: AsyncSession,
    *,
    actor: Permissions,
    target_user_id: int,
    role: RoleName,
    organization_id: int | None,
    now: datetime,
) -> bool:
    if role is RoleName.ADMIN:
        raise PermissionDeniedError(
            "Роль администратора управляется только через ADMIN_IDS в настройках окружения."
        )
    if role is RoleName.ORGANIZER and not actor.can_manage_organizers:
        raise PermissionDeniedError()
    if role is RoleName.ORG_REP and not actor.can_manage_org_representatives:
        raise PermissionDeniedError()
    if role is RoleName.ORG_REP and organization_id is None:
        raise ValidationError("Не указана организация.")

    revoked = await roles_repo.revoke(
        session,
        user_id=target_user_id,
        role=role,
        organization_id=organization_id,
        revoked_by=actor.user_id,
        at=now,
    )
    if revoked == 0:
        raise NotFoundError("Активная роль не найдена.")

    await audit_repo.log(
        session,
        entity_type="role",
        entity_id=f"{target_user_id}:{role.value}:{organization_id or 0}",
        actor_id=actor.user_id,
        action="role_revoked",
        payload={
            "user_id": target_user_id,
            "role": role.value,
            "organization_id": organization_id,
        },
        at=now,
    )
    return True


async def list_roles(session: AsyncSession, role: RoleName) -> list[Role]:
    return await roles_repo.list_active(session, role=role)
