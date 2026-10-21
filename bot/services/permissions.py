"""Role-based permissions.

Permissions are always re-checked on the server for every action, never trusted
from callback data.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.enums import RoleName
from bot.services.errors import PermissionDeniedError

if TYPE_CHECKING:
    from bot.db.models import Role


@dataclass(frozen=True)
class Permissions:
    user_id: int
    is_admin: bool = False
    is_organizer: bool = False
    org_rep_ids: frozenset[int] = frozenset()

    @property
    def is_org_rep(self) -> bool:
        return bool(self.org_rep_ids)

    @property
    def has_admin_panel(self) -> bool:
        return self.is_admin or self.is_organizer or self.is_org_rep

    @property
    def can_manage_organizers(self) -> bool:
        return self.is_admin

    @property
    def can_manage_organizations(self) -> bool:
        return self.is_admin or self.is_organizer

    @property
    def can_manage_org_representatives(self) -> bool:
        return self.is_admin or self.is_organizer

    @property
    def can_manage_raffles(self) -> bool:
        return self.is_admin or self.is_organizer

    @property
    def can_view_all_organizations(self) -> bool:
        return self.is_admin or self.is_organizer

    def can_review_organization(self, organization_id: int) -> bool:
        return self.can_view_all_organizations or organization_id in self.org_rep_ids

    def can_view_organization(self, organization_id: int) -> bool:
        return self.can_review_organization(organization_id)

    def assert_review_organization(self, organization_id: int) -> None:
        if not self.can_review_organization(organization_id):
            raise PermissionDeniedError()

    def allowed_organization_ids(self) -> frozenset[int] | None:
        """``None`` means all organizations."""
        if self.can_view_all_organizations:
            return None
        return frozenset(self.org_rep_ids)


def build_permissions(
    user_id: int,
    roles: list[Role],
    *,
    env_admin_ids: frozenset[int],
) -> Permissions:
    is_admin = user_id in env_admin_ids
    is_organizer = False
    org_rep_ids: set[int] = set()
    for role in roles:
        if role.role is RoleName.ADMIN:
            is_admin = True
        elif role.role is RoleName.ORGANIZER:
            is_organizer = True
        elif role.role is RoleName.ORG_REP and role.organization_id is not None:
            org_rep_ids.add(role.organization_id)
    return Permissions(
        user_id=user_id,
        is_admin=is_admin,
        is_organizer=is_organizer,
        org_rep_ids=frozenset(org_rep_ids),
    )


async def load_permissions(
    session: AsyncSession, user_id: int, *, env_admin_ids: frozenset[int]
) -> Permissions:
    from bot.db.repositories import roles as roles_repo

    roles = await roles_repo.active_for_user(session, user_id)
    return build_permissions(user_id, roles, env_admin_ids=env_admin_ids)
